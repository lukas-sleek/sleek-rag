def test_documentai_client_lists_processors():
    from app.documentai_client import documentai_client
    from app.config import settings

    parent = f"projects/{settings.gcp_project_id}/locations/{settings.documentai_location}"
    processors = list(documentai_client().list_processors(parent=parent))
    assert any(p.type_ == "LAYOUT_PARSER_PROCESSOR" for p in processors)
