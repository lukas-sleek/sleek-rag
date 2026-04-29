"""Plan 18.1 T3: Vertex RAG env fields load via Pydantic Settings."""

from backend.app.config import Settings


def test_vertex_rag_settings_defaults():
    s = Settings(_env_file=None)

    assert s.gcp_location == "europe-west3"
    assert s.gcs_files_bucket == "sleek-rag-files-dev"
    assert s.vertex_rag_embedding_model == "text-embedding-005"
    assert s.vertex_rag_embedding_dim == 768
    assert s.vertex_rag_generation_model == "gemini-2.5-pro"
    assert s.vertex_rag_parsing_model == "gemini-2.5-pro"
    assert s.vertex_rag_parsing_max_requests_per_min == 10


def test_vertex_rag_settings_types():
    s = Settings(_env_file=None)

    assert isinstance(s.gcp_location, str)
    assert isinstance(s.gcs_files_bucket, str)
    assert isinstance(s.vertex_rag_embedding_model, str)
    assert isinstance(s.vertex_rag_embedding_dim, int)
    assert isinstance(s.vertex_rag_generation_model, str)
    assert isinstance(s.vertex_rag_parsing_model, str)
    assert isinstance(s.vertex_rag_parsing_max_requests_per_min, int)
