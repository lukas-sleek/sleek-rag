"""Plan 18.2 T1: rag_corpus.py helpers — unit tests with mocked SDK calls."""
from __future__ import annotations

import asyncio
import types
from unittest.mock import MagicMock

import pytest

from app import rag_corpus
from app.parsing_prompts import SIA_PARSING_PROMPT


@pytest.fixture
def reset_init():
    rag_corpus._initialized = True  # skip vertexai.init in tests
    yield
    rag_corpus._initialized = False


@pytest.fixture
def fake_supabase(monkeypatch):
    """Per-test in-memory supabase stub. Tracks update payloads for assertions."""
    table = types.SimpleNamespace()

    state = {"select_data": [{}], "updates": []}

    class _Q:
        def __init__(self, table_name):
            self.table_name = table_name
            self._update_payload = None

        def select(self, *_):
            return self

        def update(self, payload):
            self._update_payload = payload
            return self

        def eq(self, *_):
            return self

        def single(self):
            return self

        def execute(self):
            if self._update_payload is not None:
                state["updates"].append((self.table_name, self._update_payload))
                return types.SimpleNamespace(data=[])
            data = state["select_data"]
            return types.SimpleNamespace(data=data[0] if data else None)

    def fake_client():
        c = MagicMock()
        c.table.side_effect = lambda name: _Q(name)
        return c

    monkeypatch.setattr(rag_corpus, "supabase", fake_client)
    return state


def test_ensure_corpus_returns_existing(reset_init, fake_supabase, monkeypatch):
    fake_supabase["select_data"] = [{"rag_corpus_name": "projects/x/locations/eu/ragCorpora/abc"}]
    monkeypatch.setattr(
        rag_corpus.rag, "create_corpus", MagicMock(side_effect=AssertionError("must not create"))
    )
    name = rag_corpus.ensure_corpus_for_project("proj-1")
    assert name == "projects/x/locations/eu/ragCorpora/abc"
    assert fake_supabase["updates"] == []


def test_ensure_corpus_creates_when_missing(reset_init, fake_supabase, monkeypatch):
    fake_supabase["select_data"] = [{"rag_corpus_name": None}]
    created = MagicMock()
    created.name = "projects/x/locations/europe-west3/ragCorpora/new"
    create_mock = MagicMock(return_value=created)
    monkeypatch.setattr(rag_corpus.rag, "create_corpus", create_mock)

    name = rag_corpus.ensure_corpus_for_project("proj-2")

    assert name == created.name
    create_mock.assert_called_once()
    kwargs = create_mock.call_args.kwargs
    assert kwargs["display_name"] == "sleek-rag-proj-2"
    backend_config = kwargs["backend_config"]
    publisher = backend_config.rag_embedding_model_config.vertex_prediction_endpoint.publisher_model
    assert "text-embedding" in publisher  # config-driven
    assert ("projects", {"rag_corpus_name": created.name}) in fake_supabase["updates"]


def test_import_pdf_passes_llm_parser_and_chunking(reset_init, monkeypatch):
    captured = {}

    async def fake_import(corpus_name, paths=None, llm_parser=None, transformation_config=None, **kw):
        captured["corpus_name"] = corpus_name
        captured["paths"] = paths
        captured["llm_parser"] = llm_parser
        captured["transformation_config"] = transformation_config
        op = MagicMock()
        op.operation = MagicMock(name="operation_proto")
        op.operation.name = "projects/x/locations/eu/operations/123"
        return op

    monkeypatch.setattr(rag_corpus.rag, "import_files_async", fake_import)

    name = asyncio.run(
        rag_corpus.import_pdf("projects/x/locations/eu/ragCorpora/c", "gs://b/u/p/f/original.pdf")
    )
    assert name == "projects/x/locations/eu/operations/123"
    assert captured["paths"] == ["gs://b/u/p/f/original.pdf"]
    assert captured["llm_parser"].custom_parsing_prompt == SIA_PARSING_PROMPT
    assert "gemini" in captured["llm_parser"].model_name.lower()
    chunking = captured["transformation_config"].chunking_config
    assert chunking.chunk_size == 1024
    assert chunking.chunk_overlap == 200


def test_delete_corpus_calls_sdk(reset_init, monkeypatch):
    delete_mock = MagicMock()
    monkeypatch.setattr(rag_corpus.rag, "delete_corpus", delete_mock)
    rag_corpus.delete_corpus("projects/x/locations/eu/ragCorpora/c")
    delete_mock.assert_called_once_with("projects/x/locations/eu/ragCorpora/c")
