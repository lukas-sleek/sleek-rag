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
    gcs_staging_bucket: str = ""

    # --- Vertex AI RAG Engine — Serverless mode (plan 20.0) ---
    # us-central1 is the only region serverless mode is published in.
    # Corpus uses RagManagedVertexVectorSearch as the vector DB; the
    # embedding model is still settable via rag_embedding_model_config
    # (Vertex doesn't force its own choice). Parsing is delegated to a
    # Document AI Layout Parser processor at `documentai_us_location`.
    gcp_location: str = "us-central1"
    gcs_files_bucket: str = "sleek-rag-files-us-dev"
    # Speech-to-Text v2 region. Chirp 2 is NOT available in multi-region
    # "eu"/"us"/"global" — only in specific regions. europe-west4 keeps
    # voice data in EU and supports chirp_2.
    gcp_stt_location: str = "europe-west4"
    gcp_stt_model: str = "chirp_2"
    gcp_stt_language: str = "de-DE"
    documentai_us_location: str = "us"
    documentai_us_processor_id: str = "452479dfc534f517"
    vertex_rag_embedding_model: str = "text-multilingual-embedding-002"

    # --- Gemini (OpenAI-compatible endpoint) ---
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    gemini_chat_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_embedding_dim: int = 768


settings = Settings()
