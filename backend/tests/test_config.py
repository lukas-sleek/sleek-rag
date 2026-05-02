"""Plan 20.0: serverless RAG settings load via Pydantic Settings."""

from app.config import Settings


def test_rag_settings_defaults():
    s = Settings(_env_file=None)

    assert s.gcp_location == "us-central1"
    assert s.gcs_files_bucket == "sleek-rag-files-us-dev"
    assert s.documentai_us_location == "us"
    assert s.documentai_us_processor_id == "452479dfc534f517"


def test_rag_settings_types():
    s = Settings(_env_file=None)

    assert isinstance(s.gcp_location, str)
    assert isinstance(s.gcs_files_bucket, str)
    assert isinstance(s.documentai_us_location, str)
    assert isinstance(s.documentai_us_processor_id, str)
