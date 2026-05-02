"""Helpers for managing per-project Vertex RAG corpora.

Plan 20.0: serverless mode in us-central1.

Each project gets exactly one corpus. New corpora use serverless mode
(`RagManagedVertexVectorSearch` + Document AI Layout Parser). Legacy EU
corpora created under plan 18.x stay reachable via `_init_vertex_for(name)`
which re-points the SDK at the corpus's actual region for queries.
"""
from __future__ import annotations

import logging
import os
import re
import threading

import vertexai
from google.oauth2 import service_account
from vertexai.preview import rag

from app.config import settings
from app.db import supabase

log = logging.getLogger(__name__)

_CORPUS_LOCATION_RE = re.compile(r"projects/[^/]+/locations/([^/]+)/ragCorpora/")

_init_lock = threading.Lock()
_active_location: str | None = None


def _set_genai_env() -> None:
    """Configure ADK's genai client to use Vertex with our service account."""
    if settings.gcp_service_account_json_path:
        os.environ.setdefault(
            "GOOGLE_APPLICATION_CREDENTIALS",
            settings.gcp_service_account_json_path,
        )
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.gcp_project_id)
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", settings.gcp_location)


def _credentials():
    if settings.gcp_service_account_json_path:
        return service_account.Credentials.from_service_account_file(
            settings.gcp_service_account_json_path
        )
    return None


def _init_vertex_at(location: str) -> None:
    """Initialise vertexai for the given location.

    `vertexai.init` is process-wide. When we operate against an existing
    corpus that lives in a different region (legacy EU projects), we
    re-init at the corpus's location for the duration of that call.
    Subsequent calls re-init back to whatever location they need.
    """
    global _active_location
    if not settings.gcp_project_id:
        raise RuntimeError("GCP_PROJECT_ID not configured")
    with _init_lock:
        if _active_location == location:
            return
        _set_genai_env()
        vertexai.init(
            project=settings.gcp_project_id,
            location=location,
            credentials=_credentials(),
        )
        _active_location = location


def _init_vertex() -> None:
    """Initialise at the default location (us-central1, serverless region)."""
    _init_vertex_at(settings.gcp_location)


def _init_vertex_for(corpus_name: str) -> str:
    """Initialise at the corpus's actual region. Returns that region.

    Required for cross-region calls against legacy EU corpora. Falls back
    to the default location if the corpus name doesn't carry a region.
    """
    m = _CORPUS_LOCATION_RE.match(corpus_name or "")
    location = m.group(1) if m else settings.gcp_location
    _init_vertex_at(location)
    return location


def _vector_db_config() -> rag.RagVectorDbConfig:
    """Serverless: RagManagedVertexVectorSearch (Vector Search 2.0).

    The vector DB is managed; the embedding model is pinned via the env
    var `VERTEX_RAG_EMBEDDING_MODEL` (default text-embedding-002). The
    SDK's set_embedding_model_config helper unpacks the *flat*
    `EmbeddingModelConfig` shape (publisher_model attr), not the nested
    `RagEmbeddingModelConfig` — passing the latter raises
    AttributeError inside `_gapic_utils.set_embedding_model_config`.
    """
    embedding = None
    if settings.vertex_rag_embedding_model:
        embedding = rag.EmbeddingModelConfig(
            publisher_model=(
                f"publishers/google/models/{settings.vertex_rag_embedding_model}"
            )
        )
    return rag.RagVectorDbConfig(
        vector_db=rag.RagManagedVertexVectorSearch(),
        rag_embedding_model_config=embedding,
    )


def _layout_parser_config() -> rag.LayoutParserConfig:
    """Document AI Layout Parser used by Vertex's import pipeline."""
    processor = (
        f"projects/{settings.gcp_project_id}"
        f"/locations/{settings.documentai_us_location}"
        f"/processors/{settings.documentai_us_processor_id}"
    )
    return rag.LayoutParserConfig(
        processor_name=processor,
        max_parsing_requests_per_min=120,
    )


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _project_lock(project_id: str) -> threading.Lock:
    """Per-project lock so concurrent uploads don't race to create N corpora."""
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
    """Return the corpus resource name for this project, creating on first call."""
    _init_vertex()
    existing = _read_corpus_name(project_id)
    if existing:
        return existing

    with _project_lock(project_id):
        existing = _read_corpus_name(project_id)
        if existing:
            return existing

        corpus = rag.create_corpus(
            display_name=f"sleek-rag-{project_id}",
            backend_config=_vector_db_config(),
        )
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


async def import_folder(corpus_name: str, folder_uri: str) -> str:
    """Trigger ingestion of every file under a GCS folder. Returns the LRO name.

    Vertex recurses the folder and ingests each PDF (and supported office
    formats) using the Document AI Layout Parser. One LRO covers the
    whole folder; per-file readiness is observed via list_rag_files.
    """
    _init_vertex_for(corpus_name)
    op = await rag.import_files_async(
        corpus_name,
        paths=[folder_uri],
        layout_parser=_layout_parser_config(),
    )
    return op.operation.name


def delete_corpus(corpus_name: str) -> None:
    """Delete a corpus AND its files (force=True). Routes to the corpus's region.

    Treats NotFound as success: the goal is "corpus is gone", and a stale
    `projects.rag_corpus_name` pointing at an already-deleted corpus must
    not block the project delete.
    """
    from google.api_core.exceptions import NotFound
    from google.cloud.aiplatform_v1beta1.services.vertex_rag_data_service import (
        VertexRagDataServiceClient,
    )
    from google.cloud.aiplatform_v1beta1.types import DeleteRagCorpusRequest

    location = _init_vertex_for(corpus_name)
    client = VertexRagDataServiceClient(
        credentials=_credentials(),
        client_options={
            "api_endpoint": f"{location}-aiplatform.googleapis.com"
        },
    )
    try:
        client.delete_rag_corpus(DeleteRagCorpusRequest(name=corpus_name, force=True))
    except NotFound:
        log.info("rag corpus %s already gone — treating as deleted", corpus_name)
