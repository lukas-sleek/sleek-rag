"""Plan 20.0: rag_lro_poller — folder-import dispatcher + display-name resolver."""
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


CORPUS = "projects/x/locations/us-central1/ragCorpora/c"
USER_ID = "user-1"
PROJECT_ID = "p"


@pytest.fixture
def fake_supabase(monkeypatch):
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


def _row(file_id="file-1", filename="report.pdf"):
    return {
        "id": file_id,
        "ingest_lro_name": "projects/x/locations/us-central1/operations/op-1",
        "filename": filename,
        "user_id": USER_ID,
        "project_id": PROJECT_ID,
        "projects": {"rag_corpus_name": CORPUS},
    }


def _payload_for(state, file_id):
    matches = [
        (table, payload, where)
        for table, payload, where in state["updates"]
        if table == "project_files" and any(("id", file_id) in w for w in [where])
    ]
    return matches[-1][1] if matches else None


def _set_op(monkeypatch, op):
    client = MagicMock()
    client.get_operation.return_value = op
    monkeypatch.setattr(rag_lro_poller, "_ops_client", lambda _c: client)


def _set_files(monkeypatch, *, files=None):
    """Patch _list_rag_files_by_display_name to return a {display: {...}} map."""
    files = files or {}
    monkeypatch.setattr(
        rag_lro_poller,
        "_list_rag_files_by_display_name",
        lambda _c: files,
    )


# --------------- _resolve_lro ---------------


def test_resolve_running_op_does_not_touch_rows(fake_supabase, monkeypatch):
    _set_files(monkeypatch)
    _set_op(monkeypatch, Operation(name="op", done=False))
    rag_lro_poller._resolve_lro("op", [_row()])
    assert fake_supabase["updates"] == []


def test_resolve_hard_error_marks_every_row_failed(fake_supabase, monkeypatch):
    _set_files(monkeypatch)
    op = Operation(name="op", done=True, error=Status(code=13, message="boom"))
    _set_op(monkeypatch, op)
    rag_lro_poller._resolve_lro("op", [_row("a", "alpha.pdf"), _row("b", "bravo.pdf")])
    payload_a = _payload_for(fake_supabase, "a")
    payload_b = _payload_for(fake_supabase, "b")
    assert payload_a["status"] == "failed" and "boom" in payload_a["ingest_error"]
    assert payload_b["status"] == "failed"


def test_resolve_per_file_partial_failures_split_correctly(fake_supabase, monkeypatch):
    """Two-file batch: one succeeds (RagFile is ACTIVE), the other fails."""
    response = ImportRagFilesResponse(failed_rag_files_count=1, imported_rag_files_count=1)
    metadata = ImportRagFilesOperationMetadata()
    metadata.generic_metadata.partial_failures.append(
        Status(code=5, message="parse error processing alpha.pdf — corrupted PDF")
    )
    op = Operation(name="op", done=True, response=_pack(response), metadata=_pack(metadata))
    _set_op(monkeypatch, op)
    # bravo.pdf is ACTIVE — alpha.pdf never appeared in the corpus.
    _set_files(monkeypatch, files={
        "bravo.pdf": {"rag_file_name": f"{CORPUS}/ragFiles/bravo-id", "state": "ACTIVE"},
    })

    bad = _row("a", "alpha.pdf")
    good = _row("b", "bravo.pdf")
    rag_lro_poller._resolve_lro("op", [bad, good])

    payload_bad = _payload_for(fake_supabase, "a")
    payload_good = _payload_for(fake_supabase, "b")
    assert payload_bad["status"] == "failed"
    assert "alpha.pdf" in payload_bad["ingest_error"]
    assert payload_good["status"] == "ready"
    assert payload_good["rag_file_name"].endswith("/ragFiles/bravo-id")


def test_resolve_full_success_marks_every_row_ready(fake_supabase, monkeypatch):
    response = ImportRagFilesResponse(imported_rag_files_count=2)
    op = Operation(name="op", done=True, response=_pack(response))
    _set_op(monkeypatch, op)
    _set_files(monkeypatch, files={
        "alpha.pdf": {"rag_file_name": f"{CORPUS}/ragFiles/A", "state": "ACTIVE"},
        "bravo.pdf": {"rag_file_name": f"{CORPUS}/ragFiles/B", "state": "ACTIVE"},
    })
    rag_lro_poller._resolve_lro("op", [_row("a", "alpha.pdf"), _row("b", "bravo.pdf")])
    a = _payload_for(fake_supabase, "a")
    b = _payload_for(fake_supabase, "b")
    assert a["status"] == "ready"
    assert b["status"] == "ready"
    assert a["rag_file_name"].endswith("/ragFiles/A")
    assert b["rag_file_name"].endswith("/ragFiles/B")


def test_resolve_in_flight_active_file_flips_before_lro_done(fake_supabase, monkeypatch):
    """Per-file readiness: row goes ready as soon as its RagFile is ACTIVE,
    even if the overall LRO is still running."""
    _set_op(monkeypatch, Operation(name="op", done=False))
    _set_files(monkeypatch, files={
        "alpha.pdf": {"rag_file_name": f"{CORPUS}/ragFiles/A", "state": "ACTIVE"},
    })
    rag_lro_poller._resolve_lro("op", [_row("a", "alpha.pdf"), _row("b", "bravo.pdf")])
    assert _payload_for(fake_supabase, "a")["status"] == "ready"
    # bravo not yet ACTIVE → still parsing → not touched while LRO running
    assert _payload_for(fake_supabase, "b") is None


# --------------- _dispatch_step ---------------


def test_dispatch_batches_queued_rows_into_folder_import(monkeypatch, fake_supabase):
    queued = [
        _row("a", "alpha.pdf"),
        _row("b", "bravo.pdf"),
    ]
    monkeypatch.setattr(rag_lro_poller, "_claim_queued_rows", lambda: queued)
    monkeypatch.setattr(rag_lro_poller, "_corpora_with_in_flight_imports", lambda: set())
    monkeypatch.setattr(rag_lro_poller, "_init_vertex_for", lambda *_a, **_k: "us-central1")
    monkeypatch.setattr(rag_lro_poller.settings, "gcs_files_bucket", "the-bucket", raising=False)

    captured = {}

    async def fake_import(corpus_name, folder_uri):
        captured["corpus"] = corpus_name
        captured["folder"] = folder_uri
        return "projects/x/locations/us-central1/operations/lro-new"

    monkeypatch.setattr(rag_lro_poller, "import_folder", fake_import)

    asyncio.run(rag_lro_poller._dispatch_step())

    assert captured["corpus"] == CORPUS
    assert captured["folder"] == f"gs://the-bucket/{USER_ID}/{PROJECT_ID}/"
    a = _payload_for(fake_supabase, "a")
    b = _payload_for(fake_supabase, "b")
    assert a["status"] == "parsing"
    assert b["status"] == "parsing"
    assert a["ingest_lro_name"] == b["ingest_lro_name"]


def test_dispatch_skips_corpora_with_in_flight_imports(monkeypatch, fake_supabase):
    queued = [_row("a", "alpha.pdf")]
    monkeypatch.setattr(rag_lro_poller, "_claim_queued_rows", lambda: queued)
    monkeypatch.setattr(
        rag_lro_poller,
        "_corpora_with_in_flight_imports",
        lambda: {CORPUS},
    )
    import_mock = MagicMock()
    monkeypatch.setattr(rag_lro_poller, "import_folder", import_mock)
    monkeypatch.setattr(rag_lro_poller, "_init_vertex_for", lambda *_a, **_k: "us-central1")

    asyncio.run(rag_lro_poller._dispatch_step())

    import_mock.assert_not_called()
    assert fake_supabase["updates"] == []
