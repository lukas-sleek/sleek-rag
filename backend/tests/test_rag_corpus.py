"""Plan 18.2 T1: rag_corpus.py helpers — unit tests with mocked SDK calls."""
from __future__ import annotations

import asyncio
import threading
import types
from unittest.mock import MagicMock

import pytest

from app import rag_corpus
from app.parsing_prompts import SIA_PARSING_PROMPT


@pytest.fixture
def reset_init():
    rag_corpus._initialized = True  # skip vertexai.init in tests
    rag_corpus._locks = {}
    yield
    rag_corpus._initialized = False
    rag_corpus._locks = {}


def _stub_supabase(monkeypatch, *, select_returns, update_returns):
    """Build a chainable supabase stub.

    select_returns: list of dicts to feed _read_corpus_name in order.
    update_returns: list of dicts (each list itself) the conditional update
                    returns; an empty list means "lost the race".
    """
    state = {"select_calls": 0, "update_calls": 0, "update_payloads": []}

    class _Q:
        def __init__(self, *_):
            self._update = None

        def select(self, *_):
            return self

        def update(self, payload):
            self._update = payload
            return self

        def eq(self, *_):
            return self

        def is_(self, *_):
            return self

        def single(self):
            return self

        def execute(self):
            if self._update is not None:
                state["update_payloads"].append(self._update)
                idx = state["update_calls"]
                state["update_calls"] += 1
                return types.SimpleNamespace(data=update_returns[idx])
            idx = state["select_calls"]
            state["select_calls"] += 1
            return types.SimpleNamespace(data=select_returns[idx])

    fake = MagicMock()
    fake.table.side_effect = lambda name: _Q(name)
    monkeypatch.setattr(rag_corpus, "supabase", lambda: fake)
    return state


def test_ensure_corpus_returns_existing(reset_init, monkeypatch):
    _stub_supabase(
        monkeypatch,
        select_returns=[{"rag_corpus_name": "projects/x/locations/eu/ragCorpora/abc"}],
        update_returns=[],
    )
    monkeypatch.setattr(
        rag_corpus.rag,
        "create_corpus",
        MagicMock(side_effect=AssertionError("must not create")),
    )
    name = rag_corpus.ensure_corpus_for_project("proj-1")
    assert name == "projects/x/locations/eu/ragCorpora/abc"


def test_ensure_corpus_creates_when_missing(reset_init, monkeypatch):
    state = _stub_supabase(
        monkeypatch,
        # First read: NULL. Second read (inside lock): also NULL.
        select_returns=[{"rag_corpus_name": None}, {"rag_corpus_name": None}],
        # Conditional update returns the row, meaning we won the race.
        update_returns=[[{"id": "proj-2", "rag_corpus_name": "projects/x/locations/europe-west3/ragCorpora/new"}]],
    )
    created = MagicMock()
    created.name = "projects/x/locations/europe-west3/ragCorpora/new"
    create_mock = MagicMock(return_value=created)
    monkeypatch.setattr(rag_corpus.rag, "create_corpus", create_mock)

    name = rag_corpus.ensure_corpus_for_project("proj-2")

    assert name == created.name
    create_mock.assert_called_once()
    kwargs = create_mock.call_args.kwargs
    assert kwargs["display_name"] == "sleek-rag-proj-2"
    publisher = (
        kwargs["backend_config"]
        .rag_embedding_model_config.vertex_prediction_endpoint.publisher_model
    )
    assert "text-embedding" in publisher
    assert state["update_payloads"][0] == {"rag_corpus_name": created.name}


def test_ensure_corpus_loses_race_deletes_duplicate(reset_init, monkeypatch):
    """Conditional update returns no row → another process won → drop our orphan."""
    winner = "projects/x/locations/eu/ragCorpora/winner"
    _stub_supabase(
        monkeypatch,
        # Read 1 (top): NULL. Read 2 (inside lock): NULL. Read 3 (post-loss): winner.
        select_returns=[
            {"rag_corpus_name": None},
            {"rag_corpus_name": None},
            {"rag_corpus_name": winner},
        ],
        # Conditional update returns [] — the rag_corpus_name was no longer NULL.
        update_returns=[[]],
    )
    duplicate = MagicMock()
    duplicate.name = "projects/x/locations/eu/ragCorpora/dup"
    monkeypatch.setattr(rag_corpus.rag, "create_corpus", MagicMock(return_value=duplicate))
    delete_mock = MagicMock()
    monkeypatch.setattr(rag_corpus.rag, "delete_corpus", delete_mock)

    name = rag_corpus.ensure_corpus_for_project("proj-race")

    assert name == winner
    delete_mock.assert_called_once_with(duplicate.name)


def test_ensure_corpus_only_creates_one_under_concurrent_callers(
    reset_init, monkeypatch
):
    """Two threads racing on the same project must yield exactly one create_corpus call."""
    select_data = {"value": None}
    creates = {"count": 0}
    updates = {"count": 0}

    class _Q:
        def __init__(self, *_):
            self._update = None

        def select(self, *_):
            return self

        def update(self, payload):
            self._update = payload
            return self

        def eq(self, *_):
            return self

        def is_(self, *_):
            return self

        def single(self):
            return self

        def execute(self):
            if self._update is not None:
                # First conditional update (NULL → set) succeeds; later ones return [].
                if select_data["value"] is None:
                    select_data["value"] = self._update["rag_corpus_name"]
                    updates["count"] += 1
                    return types.SimpleNamespace(
                        data=[{"id": "proj-c", "rag_corpus_name": select_data["value"]}]
                    )
                return types.SimpleNamespace(data=[])
            return types.SimpleNamespace(data={"rag_corpus_name": select_data["value"]})

    fake = MagicMock()
    fake.table.side_effect = lambda name: _Q(name)
    monkeypatch.setattr(rag_corpus, "supabase", lambda: fake)

    def _create(*_, **__):
        creates["count"] += 1
        c = MagicMock()
        c.name = f"projects/x/locations/eu/ragCorpora/created-{creates['count']}"
        return c

    monkeypatch.setattr(rag_corpus.rag, "create_corpus", _create)
    monkeypatch.setattr(rag_corpus.rag, "delete_corpus", MagicMock())

    results: list[str] = []

    def _worker():
        results.append(rag_corpus.ensure_corpus_for_project("proj-c"))

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 4
    assert len(set(results)) == 1, f"all callers should converge on one corpus, got {set(results)}"
    assert creates["count"] == 1, f"expected exactly 1 create_corpus call, got {creates['count']}"


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
    # Parsing model must reference the parsing-model location, not gcp_location,
    # because Gemini 2.5 Pro is not published in europe-west3.
    assert (
        f"/locations/{rag_corpus.settings.vertex_rag_parsing_model_location}/"
        in captured["llm_parser"].model_name
    )
    chunking = captured["transformation_config"].chunking_config
    assert chunking.chunk_size == 1024
    assert chunking.chunk_overlap == 200


def test_delete_corpus_calls_sdk(reset_init, monkeypatch):
    delete_mock = MagicMock()
    monkeypatch.setattr(rag_corpus.rag, "delete_corpus", delete_mock)
    rag_corpus.delete_corpus("projects/x/locations/eu/ragCorpora/c")
    delete_mock.assert_called_once_with("projects/x/locations/eu/ragCorpora/c")
