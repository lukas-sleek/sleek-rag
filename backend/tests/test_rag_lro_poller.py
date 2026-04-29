"""Plan 18.2 T5: rag.import_files LRO poller — dispatch + resolve."""
from __future__ import annotations

import asyncio
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
    any_msg = AnyProto()
    any_msg.Pack(type(msg).pb(msg))
    return any_msg


@pytest.fixture
def fake_supabase(monkeypatch):
    """Records every UPDATE so tests can assert per-row outcomes."""
    state = {"updates": []}

    class _Q:
        def __init__(self, table_name):
            self.table_name = table_name
            self._update = None
            self._where: list = []

        def update(self, payload):
            self._update = payload
            return self

        def eq(self, *args):
            self._where.append(args)
            return self

        def execute(self):
            if self._update is not None:
                state["updates"].append((self.table_name, dict(self._update), list(self._where)))
            return types.SimpleNamespace(data=[])

    fake = MagicMock()
    fake.table.side_effect = lambda name: _Q(name)
    monkeypatch.setattr(rag_lro_poller, "supabase", lambda: fake)
    return state


def _row(file_id="file-1", gcs_uri="gs://bucket/u/p/file-1/original.pdf"):
    return {
        "id": file_id,
        "ingest_lro_name": "projects/x/locations/eu/operations/op-1",
        "gcs_blob_path": gcs_uri,
        "project_id": "p",
        "projects": {"rag_corpus_name": "projects/x/locations/eu/ragCorpora/c"},
    }


def _payload_for(state, file_id):
    matches = [
        (table, payload, where)
        for table, payload, where in state["updates"]
        if table == "project_files" and any(("id", file_id) in w for w in [where])
    ]
    return matches[0][1] if matches else None


def _set_op(monkeypatch, op):
    client = MagicMock()
    client.get_operation.return_value = op
    monkeypatch.setattr(rag_lro_poller, "_ops_client", lambda: client)


# --------------- _resolve_lro ---------------


def test_resolve_running_op_does_not_touch_rows(fake_supabase, monkeypatch):
    _set_op(monkeypatch, Operation(name="op", done=False))
    rag_lro_poller._resolve_lro("op", [_row()])
    assert fake_supabase["updates"] == []


def test_resolve_hard_error_marks_every_row_failed(fake_supabase, monkeypatch):
    op = Operation(name="op", done=True, error=Status(code=13, message="boom"))
    _set_op(monkeypatch, op)
    rag_lro_poller._resolve_lro("op", [_row("a"), _row("b")])
    payload_a = _payload_for(fake_supabase, "a")
    payload_b = _payload_for(fake_supabase, "b")
    assert payload_a["status"] == "failed" and "boom" in payload_a["ingest_error"]
    assert payload_b["status"] == "failed"


def test_resolve_per_file_partial_failures_split_correctly(fake_supabase, monkeypatch):
    """Two-file batch: one fails by URI, the other goes ready."""
    response = ImportRagFilesResponse(failed_rag_files_count=1, imported_rag_files_count=1)
    metadata = ImportRagFilesOperationMetadata()
    metadata.generic_metadata.partial_failures.append(
        Status(code=5, message="404 Publisher Model gemini not found processing gs://bucket/u/p/file-bad/original.pdf")
    )
    op = Operation(name="op", done=True, response=_pack(response), metadata=_pack(metadata))
    _set_op(monkeypatch, op)
    monkeypatch.setattr(
        rag_lro_poller, "_resolve_rag_file_name",
        lambda corpus, gcs: "projects/x/locations/eu/ragCorpora/c/ragFiles/abc",
    )

    bad = _row("file-bad", "gs://bucket/u/p/file-bad/original.pdf")
    good = _row("file-good", "gs://bucket/u/p/file-good/original.pdf")
    rag_lro_poller._resolve_lro("op", [bad, good])

    payload_bad = _payload_for(fake_supabase, "file-bad")
    payload_good = _payload_for(fake_supabase, "file-good")
    assert payload_bad["status"] == "failed"
    assert "Publisher Model" in payload_bad["ingest_error"]
    assert payload_good["status"] == "ready"
    assert payload_good["rag_file_name"].endswith("/ragFiles/abc")


def test_resolve_zero_imports_no_partial_uris_fails_every_row(fake_supabase, monkeypatch):
    """Vertex returned failedRagFilesCount>0 but the partialFailures lacked URIs.

    Don't strand rows in parsing forever — fail them all with the available
    failure message.
    """
    response = ImportRagFilesResponse(failed_rag_files_count=2, imported_rag_files_count=0)
    metadata = ImportRagFilesOperationMetadata()
    metadata.generic_metadata.partial_failures.append(
        Status(code=5, message="batch failed for unknown reason")
    )
    op = Operation(name="op", done=True, response=_pack(response), metadata=_pack(metadata))
    _set_op(monkeypatch, op)
    rag_lro_poller._resolve_lro("op", [_row("a"), _row("b")])
    assert _payload_for(fake_supabase, "a")["status"] == "failed"
    assert _payload_for(fake_supabase, "b")["status"] == "failed"


def test_resolve_full_success_marks_every_row_ready(fake_supabase, monkeypatch):
    response = ImportRagFilesResponse(imported_rag_files_count=2)
    op = Operation(name="op", done=True, response=_pack(response))
    _set_op(monkeypatch, op)
    monkeypatch.setattr(
        rag_lro_poller, "_resolve_rag_file_name",
        lambda corpus, gcs: f"projects/x/locations/eu/ragCorpora/c/ragFiles/{gcs.split('/')[-2]}",
    )
    rag_lro_poller._resolve_lro(
        "op", [_row("a", "gs://bucket/u/p/a/original.pdf"), _row("b", "gs://bucket/u/p/b/original.pdf")]
    )
    assert _payload_for(fake_supabase, "a")["status"] == "ready"
    assert _payload_for(fake_supabase, "b")["status"] == "ready"


# --------------- _dispatch_step ---------------


def test_dispatch_batches_queued_rows_per_corpus_into_one_lro(monkeypatch, fake_supabase):
    queued = [
        _row("a", "gs://bucket/u/p/a/original.pdf"),
        _row("b", "gs://bucket/u/p/b/original.pdf"),
    ]
    monkeypatch.setattr(rag_lro_poller, "_claim_queued_rows", lambda: queued)
    monkeypatch.setattr(rag_lro_poller, "_corpora_with_in_flight_imports", lambda: set())

    captured = {}

    async def fake_import(corpus_name, uris):
        captured["corpus_name"] = corpus_name
        captured["uris"] = uris
        return "projects/x/locations/eu/operations/lro-new"

    monkeypatch.setattr(rag_lro_poller, "import_pdfs", fake_import)

    asyncio.run(rag_lro_poller._dispatch_step())

    assert captured["corpus_name"] == "projects/x/locations/eu/ragCorpora/c"
    assert captured["uris"] == [
        "gs://bucket/u/p/a/original.pdf",
        "gs://bucket/u/p/b/original.pdf",
    ]
    # both rows transitioned to parsing with the same LRO name
    a = _payload_for(fake_supabase, "a")
    b = _payload_for(fake_supabase, "b")
    assert a["status"] == "parsing"
    assert b["status"] == "parsing"
    assert a["ingest_lro_name"] == b["ingest_lro_name"] == "projects/x/locations/eu/operations/lro-new"


def test_dispatch_skips_corpora_with_in_flight_imports(monkeypatch, fake_supabase):
    queued = [_row("a", "gs://bucket/u/p/a/original.pdf")]
    monkeypatch.setattr(rag_lro_poller, "_claim_queued_rows", lambda: queued)
    monkeypatch.setattr(
        rag_lro_poller,
        "_corpora_with_in_flight_imports",
        lambda: {"projects/x/locations/eu/ragCorpora/c"},
    )
    import_mock = MagicMock()
    monkeypatch.setattr(rag_lro_poller, "import_pdfs", import_mock)

    asyncio.run(rag_lro_poller._dispatch_step())

    import_mock.assert_not_called()
    assert fake_supabase["updates"] == []
