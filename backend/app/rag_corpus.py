"""Helpers for managing per-project Vertex RAG corpora.

Plan 18.2: every project gets exactly one corpus (Q4 in 18.0 master spec).
Corpus is lazy-created on first upload, persisted via projects.rag_corpus_name,
and deleted on project deletion.
"""
from __future__ import annotations

import vertexai
from google.oauth2 import service_account
from vertexai.preview import rag

from app.config import settings
from app.db import supabase
from app.parsing_prompts import SIA_PARSING_PROMPT

_initialized = False


def _init_vertex() -> None:
    """Lazy vertexai.init using the existing service account JSON key."""
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
    parsing_model = (
        f"projects/{settings.gcp_project_id}"
        f"/locations/{settings.gcp_location}"
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


def ensure_corpus_for_project(project_id: str) -> str:
    """Return the corpus resource name for this project, creating it on first call."""
    _init_vertex()
    row = (
        supabase()
        .table("projects")
        .select("rag_corpus_name")
        .eq("id", project_id)
        .single()
        .execute()
    )
    existing = (row.data or {}).get("rag_corpus_name")
    if existing:
        return existing

    corpus = rag.create_corpus(
        display_name=f"sleek-rag-{project_id}",
        backend_config=rag.RagVectorDbConfig(
            rag_embedding_model_config=_embedding_config()
        ),
    )
    supabase().table("projects").update(
        {"rag_corpus_name": corpus.name}
    ).eq("id", project_id).execute()
    return corpus.name


async def import_pdf(corpus_name: str, gcs_uri: str) -> str:
    """Trigger ingestion. Returns the LRO operation name for polling."""
    _init_vertex()
    op = await rag.import_files_async(
        corpus_name,
        paths=[gcs_uri],
        llm_parser=_llm_parser_config(),
        transformation_config=_transformation_config(),
    )
    return op.operation.name


def delete_corpus(corpus_name: str) -> None:
    _init_vertex()
    rag.delete_corpus(corpus_name)
