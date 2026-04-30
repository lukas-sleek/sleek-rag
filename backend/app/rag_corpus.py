"""Helpers for managing per-project Vertex RAG corpora.

Plan 18.2: every project gets exactly one corpus (Q4 in 18.0 master spec).
Corpus is lazy-created on first upload, persisted via projects.rag_corpus_name,
and deleted on project deletion.
"""
from __future__ import annotations

import logging
import os
import threading

import vertexai
from google.oauth2 import service_account
from vertexai.preview import rag

from app.config import settings
from app.db import supabase
from app.parsing_prompts import SIA_PARSING_PROMPT

log = logging.getLogger(__name__)

_initialized = False


def _init_vertex() -> None:
    """Lazy vertexai.init using the existing service account JSON key.

    Also exports `GOOGLE_APPLICATION_CREDENTIALS` so every downstream Google
    client that resolves credentials via `google.auth.default()` (notably
    google-adk's google_llm path, which calls Gemini directly and does NOT
    inherit `vertexai.init()` credentials) picks up the same service account.
    """
    global _initialized
    if _initialized:
        return
    if not settings.gcp_project_id:
        raise RuntimeError("GCP_PROJECT_ID not configured")
    creds = None
    if settings.gcp_service_account_json_path:
        creds = service_account.Credentials.from_service_account_file(
            settings.gcp_service_account_json_path
        )
        os.environ.setdefault(
            "GOOGLE_APPLICATION_CREDENTIALS",
            settings.gcp_service_account_json_path,
        )
        # ADK's genai client honours these to skip ADC entirely and force
        # the Vertex AI endpoint (instead of the Generative Language API,
        # which doesn't accept service-account creds).
        #
        # Location override: gemini-2.5-pro (used by chat_orchestrator) is
        # NOT published in europe-west3. We pin the genai client to `global`
        # so both Pro and Flash are reachable. RAG retrieval still uses
        # europe-west3 because `vertexai.preview.rag` reads its location
        # from `vertexai.init()` (call below), not from these env vars.
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.gcp_project_id)
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
    vertexai.init(
        project=settings.gcp_project_id,
        location=settings.gcp_location,
        credentials=creds,
    )
    _initialized = True


def _embedding_config() -> rag.RagEmbeddingModelConfig:
    return rag.RagEmbeddingModelConfig(
        vertex_prediction_endpoint=rag.VertexPredictionEndpoint(
            publisher_model=(
                f"publishers/google/models/{settings.vertex_rag_embedding_model}"
            )
        )
    )


def _llm_parser_config() -> rag.LlmParserConfig:
    # Parsing model lives in `vertex_rag_parsing_model_location` (default
    # "global"), independent of the corpus region — Gemini 2.5 Pro is not
    # published in europe-west3.
    parsing_model = (
        f"projects/{settings.gcp_project_id}"
        f"/locations/{settings.vertex_rag_parsing_model_location}"
        f"/publishers/google/models/{settings.vertex_rag_parsing_model}"
    )
    return rag.LlmParserConfig(
        model_name=parsing_model,
        max_parsing_requests_per_min=settings.vertex_rag_parsing_max_requests_per_min,
        custom_parsing_prompt=SIA_PARSING_PROMPT,
    )


def _transformation_config() -> rag.TransformationConfig:
    return rag.TransformationConfig(
        chunking_config=rag.ChunkingConfig(chunk_size=1024, chunk_overlap=200)
    )


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _project_lock(project_id: str) -> threading.Lock:
    """Per-project lock so concurrent uploads don't race to create N corpora.

    Process-local. Multi-process deploys still need the post-create
    reconciliation below as a backstop.
    """
    with _locks_guard:
        lock = _locks.get(project_id)
        if lock is None:
            lock = threading.Lock()
            _locks[project_id] = lock
        return lock


def _read_corpus_name(project_id: str) -> str | None:
    row = (
        supabase()
        .table("projects")
        .select("rag_corpus_name")
        .eq("id", project_id)
        .single()
        .execute()
    )
    return (row.data or {}).get("rag_corpus_name")


def ensure_corpus_for_project(project_id: str) -> str:
    """Return the corpus resource name for this project, creating on first call.

    Race-safe: a per-project in-process lock serialises concurrent callers,
    and a post-create read+reconcile step deletes any duplicate corpus that
    sneaks past (e.g. multi-process worker setups).
    """
    _init_vertex()
    existing = _read_corpus_name(project_id)
    if existing:
        return existing

    with _project_lock(project_id):
        # Re-check inside the lock — another thread may have created it
        # between our read above and acquiring the lock.
        existing = _read_corpus_name(project_id)
        if existing:
            return existing

        corpus = rag.create_corpus(
            display_name=f"sleek-rag-{project_id}",
            backend_config=rag.RagVectorDbConfig(
                rag_embedding_model_config=_embedding_config()
            ),
        )
        # Conditional update: only set rag_corpus_name if still NULL. If
        # another process already wrote one (multi-worker race), this
        # update returns no row and we delete our duplicate.
        upd = (
            supabase()
            .table("projects")
            .update({"rag_corpus_name": corpus.name})
            .eq("id", project_id)
            .is_("rag_corpus_name", "null")
            .execute()
        )
        if upd.data:
            return corpus.name

        # Lost the race: another process beat us to the persisted name.
        # Drop our orphan corpus and return the persisted one.
        log.warning(
            "lost ensure_corpus race for project %s; deleting duplicate %s",
            project_id, corpus.name,
        )
        try:
            rag.delete_corpus(corpus.name)
        except Exception:
            log.exception("failed to delete duplicate corpus %s", corpus.name)
        winner = _read_corpus_name(project_id)
        if not winner:
            raise RuntimeError(
                f"corpus reconciliation failed for project {project_id}"
            )
        return winner


async def import_pdf(corpus_name: str, gcs_uri: str) -> str:
    """Trigger ingestion of a single PDF. Returns the LRO operation name."""
    return await import_pdfs(corpus_name, [gcs_uri])


async def import_pdfs(corpus_name: str, gcs_uris: list[str]) -> str:
    """Trigger a batched import. One LRO covers every path.

    Vertex serialises operations per corpus, so concurrent uploads must
    share a single LRO instead of firing one each (which 409s with
    FailedPrecondition).
    """
    _init_vertex()
    op = await rag.import_files_async(
        corpus_name,
        paths=gcs_uris,
        llm_parser=_llm_parser_config(),
        transformation_config=_transformation_config(),
    )
    return op.operation.name


def delete_corpus(corpus_name: str) -> None:
    """Delete a corpus AND its files. The SDK wrapper refuses non-empty corpora."""
    _init_vertex()
    from google.cloud.aiplatform_v1beta1.services.vertex_rag_data_service import (
        VertexRagDataServiceClient,
    )
    from google.cloud.aiplatform_v1beta1.types import DeleteRagCorpusRequest

    creds = None
    if settings.gcp_service_account_json_path:
        creds = service_account.Credentials.from_service_account_file(
            settings.gcp_service_account_json_path
        )
    client = VertexRagDataServiceClient(
        credentials=creds,
        client_options={
            "api_endpoint": f"{settings.gcp_location}-aiplatform.googleapis.com"
        },
    )
    client.delete_rag_corpus(DeleteRagCorpusRequest(name=corpus_name, force=True))
