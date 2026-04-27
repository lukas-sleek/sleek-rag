from google.cloud import documentai_v1 as documentai
from google.oauth2 import service_account

from app.config import settings

_client = None


def documentai_client() -> documentai.DocumentProcessorServiceClient:
    global _client
    if _client is None:
        creds = service_account.Credentials.from_service_account_file(
            settings.gcp_service_account_json_path
        )
        opts = {"api_endpoint": f"{settings.documentai_location}-documentai.googleapis.com"}
        _client = documentai.DocumentProcessorServiceClient(
            credentials=creds, client_options=opts
        )
    return _client


def processor_name() -> str:
    return (
        f"projects/{settings.gcp_project_id}"
        f"/locations/{settings.documentai_location}"
        f"/processors/{settings.documentai_processor_id}"
    )
