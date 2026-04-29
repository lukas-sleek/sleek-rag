"""Plan 18.2 T7: project deletion cascades into corpus + GCS prefix delete."""
from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.routers import projects as projects_router


@pytest.fixture
def client(monkeypatch):
    async def _noop_poller():
        return

    monkeypatch.setattr(main_module, "run_poller", _noop_poller)
    main_module.app.dependency_overrides[projects_router.current_user_id] = lambda: "user-1"
    yield TestClient(main_module.app)
    main_module.app.dependency_overrides.clear()


@pytest.fixture
def fake_supabase(monkeypatch):
    state = {
        "row": {"rag_corpus_name": "projects/x/locations/eu/ragCorpora/c"},
        "calls": [],
    }

    class _Q:
        def __init__(self, name):
            self.name = name
            self._mode = "select"

        def select(self, *_):
            self._mode = "select"
            return self

        def delete(self):
            self._mode = "delete"
            return self

        def eq(self, *_):
            return self

        def limit(self, *_):
            return self

        def execute(self):
            state["calls"].append((self.name, self._mode))
            if self._mode == "delete":
                return types.SimpleNamespace(data=[{"id": "proj-1"}])
            return types.SimpleNamespace(data=[state["row"]])

    fake = MagicMock()
    fake.table.side_effect = lambda name: _Q(name)
    monkeypatch.setattr(projects_router, "supabase", lambda: fake)
    return state


def test_delete_project_cascades_to_corpus_and_gcs(client, fake_supabase, monkeypatch):
    delete_corpus = MagicMock()
    delete_prefix = MagicMock(return_value=3)
    monkeypatch.setattr(projects_router, "delete_corpus", delete_corpus)
    monkeypatch.setattr(projects_router, "delete_prefix", delete_prefix)

    res = client.delete("/api/projects/proj-1")

    assert res.status_code == 200
    delete_corpus.assert_called_once_with("projects/x/locations/eu/ragCorpora/c")
    delete_prefix.assert_called_once_with("user-1/proj-1/")
    # Corpus + GCS happen before the DB delete
    modes = [m for _, m in fake_supabase["calls"]]
    assert modes == ["select", "delete"]


def test_delete_project_without_corpus_skips_corpus_call(client, fake_supabase, monkeypatch):
    fake_supabase["row"] = {"rag_corpus_name": None}
    delete_corpus = MagicMock()
    delete_prefix = MagicMock(return_value=0)
    monkeypatch.setattr(projects_router, "delete_corpus", delete_corpus)
    monkeypatch.setattr(projects_router, "delete_prefix", delete_prefix)

    res = client.delete("/api/projects/proj-1")

    assert res.status_code == 200
    delete_corpus.assert_not_called()
    delete_prefix.assert_called_once()


def test_delete_project_continues_when_corpus_delete_fails(
    client, fake_supabase, monkeypatch
):
    monkeypatch.setattr(
        projects_router,
        "delete_corpus",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    monkeypatch.setattr(projects_router, "delete_prefix", MagicMock(return_value=0))

    res = client.delete("/api/projects/proj-1")
    assert res.status_code == 200
