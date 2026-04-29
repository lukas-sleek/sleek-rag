"""Plan 18.2 T5: rag.import_files LRO poller — running / success / failed."""
from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest
from google.longrunning.operations_pb2 import Operation
from google.rpc.status_pb2 import Status

from app.workers import rag_lro_poller


@pytest.fixture
def fake_supabase(monkeypatch):
    state = {"updates": []}

    class _Q:
        def __init__(self, table_name):
            self.table_name = table_name
            self._update = None

        def update(self, payload):
            self._update = payload
            return self

        def eq(self, *_):
            return self

        def execute(self):
            if self._update is not None:
                state["updates"].append((self.table_name, self._update))
            return types.SimpleNamespace(data=[])

    fake = MagicMock()
    fake.table.side_effect = lambda name: _Q(name)
    monkeypatch.setattr(rag_lro_poller, "supabase", lambda: fake)
    return state


def _row(**over):
    base = {
        "id": "file-1",
        "ingest_lro_name": "projects/x/locations/eu/operations/op-1",
        "gcs_blob_path": "gs://bucket/u/p/file-1/original.pdf",
        "project_id": "p",
        "projects": {"rag_corpus_name": "projects/x/locations/eu/ragCorpora/c"},
    }
    base.update(over)
    return base


def test_poll_running_op_does_not_touch_row(fake_supabase, monkeypatch):
    op = Operation(name="op", done=False)
    client = MagicMock()
    client.get_operation.return_value = op
    monkeypatch.setattr(rag_lro_poller, "_ops_client", lambda: client)

    rag_lro_poller._poll_one(_row())

    assert fake_supabase["updates"] == []


def test_poll_failed_op_marks_row_failed(fake_supabase, monkeypatch):
    op = Operation(
        name="op",
        done=True,
        error=Status(code=13, message="parser blew up"),
    )
    client = MagicMock()
    client.get_operation.return_value = op
    monkeypatch.setattr(rag_lro_poller, "_ops_client", lambda: client)

    rag_lro_poller._poll_one(_row())

    assert len(fake_supabase["updates"]) == 1
    table, payload = fake_supabase["updates"][0]
    assert table == "project_files"
    assert payload["status"] == "failed"
    assert "parser blew up" in payload["ingest_error"]
    assert payload["ingest_lro_name"] is None


def test_poll_succeeded_op_marks_row_ready_and_sets_rag_file_name(
    fake_supabase, monkeypatch
):
    op = Operation(name="op", done=True)
    client = MagicMock()
    client.get_operation.return_value = op
    monkeypatch.setattr(rag_lro_poller, "_ops_client", lambda: client)

    monkeypatch.setattr(
        rag_lro_poller,
        "_resolve_rag_file_name",
        lambda corpus, gcs: "projects/x/locations/eu/ragCorpora/c/ragFiles/f-1",
    )

    rag_lro_poller._poll_one(_row())

    assert len(fake_supabase["updates"]) == 1
    table, payload = fake_supabase["updates"][0]
    assert table == "project_files"
    assert payload["status"] == "ready"
    assert payload["rag_file_name"] == "projects/x/locations/eu/ragCorpora/c/ragFiles/f-1"
    assert payload["ingest_lro_name"] is None


def test_poll_succeeded_without_rag_file_resolution_still_marks_ready(
    fake_supabase, monkeypatch
):
    op = Operation(name="op", done=True)
    client = MagicMock()
    client.get_operation.return_value = op
    monkeypatch.setattr(rag_lro_poller, "_ops_client", lambda: client)
    monkeypatch.setattr(rag_lro_poller, "_resolve_rag_file_name", lambda *_: None)

    rag_lro_poller._poll_one(_row())

    table, payload = fake_supabase["updates"][0]
    assert payload["status"] == "ready"
    assert "rag_file_name" not in payload
