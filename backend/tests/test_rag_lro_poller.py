"""Plan 18.2 T5: rag.import_files LRO poller — running / success / failed."""
from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest
from google.cloud.aiplatform_v1beta1.types.vertex_rag_data_service import (
    ImportRagFilesOperationMetadata,
    ImportRagFilesResponse,
)
from google.longrunning.operations_pb2 import Operation
from google.protobuf.any_pb2 import Any as AnyProto
from google.rpc.status_pb2 import Status

from app.workers import rag_lro_poller


def _pack(msg) -> AnyProto:
    """Wrap a message proto in google.protobuf.Any (proto-plus → raw pb)."""
    any_msg = AnyProto()
    any_msg.Pack(type(msg).pb(msg))
    return any_msg


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


def test_poll_op_with_failed_files_marks_row_failed(fake_supabase, monkeypatch):
    """done=true, no top-level error, but failedRagFilesCount>0 = real failure."""
    response = ImportRagFilesResponse(failed_rag_files_count=1)
    metadata = ImportRagFilesOperationMetadata()
    metadata.generic_metadata.partial_failures.append(
        Status(code=5, message="404 Publisher Model gemini-2.5-pro not found in europe-west3")
    )
    op = Operation(name="op", done=True, response=_pack(response), metadata=_pack(metadata))
    client = MagicMock()
    client.get_operation.return_value = op
    monkeypatch.setattr(rag_lro_poller, "_ops_client", lambda: client)

    rag_lro_poller._poll_one(_row())

    assert len(fake_supabase["updates"]) == 1
    table, payload = fake_supabase["updates"][0]
    assert payload["status"] == "failed"
    assert "1 failed" in payload["ingest_error"]
    assert "Publisher Model" in payload["ingest_error"]
    assert payload["ingest_lro_name"] is None


def test_poll_op_with_imported_count_zero_marks_failed(fake_supabase, monkeypatch):
    """No imports, no failures explicitly counted: still surface as failure."""
    response = ImportRagFilesResponse(failed_rag_files_count=0, imported_rag_files_count=0)
    op = Operation(name="op", done=True, response=_pack(response))
    client = MagicMock()
    client.get_operation.return_value = op
    monkeypatch.setattr(rag_lro_poller, "_ops_client", lambda: client)

    rag_lro_poller._poll_one(_row())

    payload = fake_supabase["updates"][0][1]
    assert payload["status"] == "failed"


def test_poll_op_with_imported_count_one_marks_ready(fake_supabase, monkeypatch):
    response = ImportRagFilesResponse(imported_rag_files_count=1)
    op = Operation(name="op", done=True, response=_pack(response))
    client = MagicMock()
    client.get_operation.return_value = op
    monkeypatch.setattr(rag_lro_poller, "_ops_client", lambda: client)
    monkeypatch.setattr(
        rag_lro_poller, "_resolve_rag_file_name",
        lambda corpus, gcs: "projects/x/locations/eu/ragCorpora/c/ragFiles/f-1",
    )

    rag_lro_poller._poll_one(_row())

    payload = fake_supabase["updates"][0][1]
    assert payload["status"] == "ready"
    assert payload["rag_file_name"].endswith("/ragFiles/f-1")


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
