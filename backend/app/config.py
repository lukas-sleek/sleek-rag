from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT_ENV), extra="ignore")

    cors_origins: str = "http://localhost:3000"

    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""

    @property
    def supabase_jwks_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    langsmith_api_key: str = ""
    langsmith_project: str = "sleek-rag"
    langsmith_endpoint: str = ""

    # --- Google Cloud ---
    gcp_project_id: str = ""
    gcp_service_account_json_path: str = ""  # absolute path to JSON
    documentai_location: str = "eu"
    documentai_processor_id: str = ""
    gcs_staging_bucket: str = ""

    # --- Vertex AI RAG Engine (plan 18.x migration) ---
    gcp_location: str = "europe-west3"
    gcs_files_bucket: str = "sleek-rag-files-dev"
    vertex_rag_embedding_model: str = "text-embedding-005"
    vertex_rag_embedding_dim: int = 768
    vertex_rag_generation_model: str = "gemini-2.5-pro"
    # Parsing model: Flash, not Pro. Pro is ~3-4x slower per page; for our
    # SIA layout-extraction prompt the quality difference is negligible
    # while ingestion latency was the user-facing pain point. Override via
    # VERTEX_RAG_PARSING_MODEL=gemini-2.5-pro if a corpus needs the deeper
    # reasoning Pro brings.
    vertex_rag_parsing_model: str = "gemini-2.5-flash"
    # Pin parsing to the project's home region (same as gcp_location). Flash
    # is published in europe-west3, and the regional DSQ pool gives better
    # burst headroom than `global` (see backend/scripts/dsq_diagnose.py).
    # If you ever switch parsing back to Pro (not published in eu-west3),
    # override to `global` via env.
    vertex_rag_parsing_model_location: str = "europe-west3"
    # Client-side rate limiter on the LLM Parser fan-out during ingestion.
    # Google's own LLM-parser example uses 100; Layout-Parser example uses
    # 120. Both well within Tier 1 Flash baseline (2M TPM = ~1300 RPM at
    # ~1500 tokens/page). 10 was conservative for Pro; 100 unblocks
    # meaningful parallelism without provoking DSQ throttles, since
    # ingestion rarely overlaps with active chat traffic.
    vertex_rag_parsing_max_requests_per_min: int = 100

    # --- Gemini (OpenAI-compatible endpoint) ---
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    gemini_chat_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_embedding_dim: int = 768

    # --- Retrieval (plan 16: hybrid + rerank) ---
    # "hybrid" runs vector + FTS via match_chunks_hybrid, then reranks the top
    # `pre_rerank_k` candidates down to the model's requested top_k via Vertex
    # AI Ranking API. "vector_only" calls the same RPC with an empty query
    # string (degenerate vector-only path), no rerank — escape hatch back to
    # plan-14 behavior.
    retrieval_mode: str = "hybrid"
    # Plan 17: bumped 30→80 to match Vertex AI Ranking guidance for
    # aggregation Q&A — retrieve 50–100 candidates, keep 15–20.
    pre_rerank_k: int = 80
    rerank_model: str = "semantic-ranker-default-004"
    rerank_timeout_sec: float = 4.0
    # Projektanalyse v1 batch path (plan 16 T7): more candidates + bigger
    # final context per question than the chat path can afford.
    projektanalyse_top_k: int = 15
    projektanalyse_pre_rerank_k: int = 80

    # Plan 16 T6: when the user's question matches a "welche/wer" pattern
    # AND is ≤8 tokens, expand to 2-3 synonym sub-queries via a fast Gemini
    # call, run hybrid RPC per sub-query, RRF-merge the unions before
    # rerank. Closes the synonym-cluster gap (Bauherr ↔ Grundeigentümer,
    # Drittprojekt ↔ Schnittstellenprojekt) deterministically — doesn't rely
    # on the chat agent deciding to retry.
    query_expansion: bool = True


settings = Settings()
