from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT_ENV), extra="ignore")

    cors_origins: str = "http://localhost:3000"

    openai_api_key: str = ""
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

    # --- Gemini (OpenAI-compatible endpoint) ---
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    gemini_chat_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_embedding_dim: int = 768


settings = Settings()
