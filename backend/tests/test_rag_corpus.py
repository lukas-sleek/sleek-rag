"""Plan 20.0: rag_corpus.py serverless helpers — unit tests with mocked SDK calls."""
from __future__ import annotations

import asyncio
import threading
import types
from unittest.mock import MagicMock

import pytest

from app import rag_corpus


@pytest.fixture
def reset_init(monkeypatch):
    rag_corpus._active_location = "us-central1"  # skip vertexai.init in tests
    rag_corpus._locks = {}
    monkeypatch.setattr(rag_corpus, "_init_vertex_at", lambda *_args, **_kw: None)
    yield
    rag_corpus._active_location = None
    rag_corpus._locks = {}


def _stub_supabase(monkeypatch, *, select_returns, update_returns):
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
        select_returns=[{"rag_corpus_name": "projects/x/locations/us-central1/ragCorpora/abc"}],
        update_returns=[],
    )
    monkeypatch.setattr(
        rag_corpus.rag,
        "create_corpus",
        MagicMock(side_effect=AssertionError("must not create")),
    )
    name = rag_corpus.ensure_corpus_for_project("proj-1")
    assert name == "projects/x/locations/us-central1/ragCorpora/abc"


def test_ensure_corpus_creates_with_serverless_vector_db(reset_init, monkeypatch):
    state = _stub_supabase(
        monkeypatch,
        select_returns=[{"rag_corpus_name": None}, {"rag_corpus_name": None}],
        update_returns=[[{"id": "proj-2", "rag_corpus_name": "projects/x/locations/us-central1/ragCorpora/new"}]],
    )
    created = MagicMock()
    created.name = "projects/x/locations/us-central1/ragCorpora/new"
    create_mock = MagicMock(return_value=created)
    monkeypatch.setattr(rag_corpus.rag, "create_corpus", create_mock)
    monkeypatch.setattr(
        rag_corpus.settings, "vertex_rag_embedding_model", "text-multilingual-embedding-002",
        raising=False,
    )

    name = rag_corpus.ensure_corpus_for_project("proj-2")

    assert name == created.name
    create_mock.assert_called_once()
    kwargs = create_mock.call_args.kwargs
    assert kwargs["display_name"] == "sleek-rag-proj-2"
    # Backend config carries the serverless vector DB AND the chosen embedder.
    backend_config = kwargs["backend_config"]
    assert isinstance(backend_config.vector_db, rag_corpus.rag.RagManagedVertexVectorSearch)
    assert backend_config.rag_embedding_model_config is not None
    # Flat EmbeddingModelConfig shape (the only one the SDK helper unpacks).
    publisher = backend_config.rag_embedding_model_config.publisher_model
    assert publisher.endswith("/text-multilingual-embedding-002")
    assert state["update_payloads"][0] == {"rag_corpus_name": created.name}


def test_ensure_corpus_loses_race_deletes_duplicate(reset_init, monkeypatch):
    winner = "projects/x/locations/us-central1/ragCorpora/winner"
    _stub_supabase(
        monkeypatch,
        select_returns=[
            {"rag_corpus_name": None},
            {"rag_corpus_name": None},
            {"rag_corpus_name": winner},
        ],
        update_returns=[[]],
    )
    duplicate = MagicMock()
    duplicate.name = "projects/x/locations/us-central1/ragCorpora/dup"
    monkeypatch.setattr(rag_corpus.rag, "create_corpus", MagicMock(return_value=duplicate))
    delete_mock = MagicMock()
    monkeypatch.setattr(rag_corpus.rag, "delete_corpus", delete_mock)

    name = rag_corpus.ensure_corpus_for_project("proj-race")

    assert name == winner
    delete_mock.assert_called_once_with(duplicate.name)


def test_ensure_corpus_only_creates_one_under_concurrent_callers(reset_init, monkeypatch):
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
        c.name = f"projects/x/locations/us-central1/ragCorpora/created-{creates['count']}"
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
    assert len(set(results)) == 1
    assert creates["count"] == 1


def test_import_folder_passes_layout_parser(reset_init, monkeypatch):
    captured = {}

    async def fake_import(corpus_name, paths=None, layout_parser=None, **kw):
        captured["corpus_name"] = corpus_name
        captured["paths"] = paths
        captured["layout_parser"] = layout_parser
        op = MagicMock()
        op.operation = MagicMock(name="operation_proto")
        op.operation.name = "projects/x/locations/us-central1/operations/123"
        return op

    monkeypatch.setattr(rag_corpus.rag, "import_files_async", fake_import)
    monkeypatch.setattr(rag_corpus.settings, "documentai_us_processor_id", "proc-id-xyz", raising=False)
    monkeypatch.setattr(rag_corpus.settings, "documentai_us_location", "us", raising=False)

    folder = "gs://sleek-rag-files-us-dev/u/p/"
    name = asyncio.run(
        rag_corpus.import_folder("projects/x/locations/us-central1/ragCorpora/c", folder)
    )
    assert name == "projects/x/locations/us-central1/operations/123"
    assert captured["paths"] == [folder]
    parser = captured["layout_parser"]
    assert parser is not None
    assert "/processors/proc-id-xyz" in parser.processor_name
    assert "/locations/us/" in parser.processor_name


def test_delete_corpus_uses_force_true_at_corpus_region(reset_init, monkeypatch):
    captured = {}
    fake_client = MagicMock()
    fake_client.delete_rag_corpus.side_effect = lambda req: captured.setdefault("req", req)
    fake_ctor = MagicMock(return_value=fake_client)
    monkeypatch.setattr(
        "google.cloud.aiplatform_v1beta1.services.vertex_rag_data_service.VertexRagDataServiceClient",
        fake_ctor,
    )

    # Corpus in europe-west3 (legacy) — endpoint must follow the corpus region.
    rag_corpus.delete_corpus("projects/x/locations/europe-west3/ragCorpora/c")

    fake_client.delete_rag_corpus.assert_called_once()
    req = captured["req"]
    assert req.name == "projects/x/locations/europe-west3/ragCorpora/c"
    assert req.force is True
    endpoint = fake_ctor.call_args.kwargs["client_options"]["api_endpoint"]
    assert endpoint == "europe-west3-aiplatform.googleapis.com"


def test_delete_corpus_treats_notfound_as_success(reset_init, monkeypatch):
    """Stale projects.rag_corpus_name pointing at a deleted corpus must not
    block project deletion — NotFound from Vertex is a no-op."""
    from google.api_core.exceptions import NotFound

    fake_client = MagicMock()
    fake_client.delete_rag_corpus.side_effect = NotFound("gone")
    monkeypatch.setattr(
        "google.cloud.aiplatform_v1beta1.services.vertex_rag_data_service.VertexRagDataServiceClient",
        MagicMock(return_value=fake_client),
    )

    # Should not raise.
    rag_corpus.delete_corpus("projects/x/locations/europe-west6/ragCorpora/c")
    fake_client.delete_rag_corpus.assert_called_once()


def test_delete_corpus_propagates_other_errors(reset_init, monkeypatch):
    fake_client = MagicMock()
    fake_client.delete_rag_corpus.side_effect = RuntimeError("permission denied")
    monkeypatch.setattr(
        "google.cloud.aiplatform_v1beta1.services.vertex_rag_data_service.VertexRagDataServiceClient",
        MagicMock(return_value=fake_client),
    )

    with pytest.raises(RuntimeError, match="permission denied"):
        rag_corpus.delete_corpus("projects/x/locations/us-central1/ragCorpora/c")
