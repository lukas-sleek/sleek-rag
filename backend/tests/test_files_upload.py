"""Plan 18.2 T3: file-upload endpoint uploads to GCS, ensures the corpus,
and queues the row (status='queued'). The poller's dispatcher batches
queued rows into one rag.import_files_async LRO per corpus per tick."""
from __future__ import annotations

import io
import types
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.routers import files as files_router


@pytest.fixture
def client(monkeypatch):
    # Skip the LRO poller startup; we don't want background tasks during tests.
    async def _noop_poller():
        return

    monkeypatch.setattr(main_module, "run_poller", _noop_poller)
    # Stub auth
    main_module.app.dependency_overrides[files_router.current_user_id] = lambda: "user-1"
    yield TestClient(main_module.app)
    main_module.app.dependency_overrides.clear()


@pytest.fixture
def fake_supabase(monkeypatch):
    """In-memory supabase stub. Captures inserts so the test can assert payload."""
    state = {
        "project": {"id": "proj-1", "name": "P"},
        "inserts": [],
    }

    class _Q:
        def __init__(self, table_name):
            self.table_name = table_name
            self._insert = None
            self._where = []

        def select(self, *_):
            return self

        def insert(self, payload):
            self._insert = payload
            return self

        def update(self, payload):
            self._insert = ("update", payload)
            return self

        def eq(self, *args):
            self._where.append(args)
            return self

        def limit(self, *_):
            return self

        def single(self):
            return self

        def execute(self):
            if self._insert is not None and not isinstance(self._insert, tuple):
                state["inserts"].append((self.table_name, self._insert))
                # Echo the row back as if Postgres assigned defaults.
                row = dict(self._insert)
                return types.SimpleNamespace(data=[row])
            if self.table_name == "projects":
                return types.SimpleNamespace(data=[state["project"]])
            return types.SimpleNamespace(data=[])

    fake = MagicMock()
    fake.table.side_effect = lambda name: _Q(name)

    monkeypatch.setattr(files_router, "supabase", lambda: fake)
    return state


def test_upload_pdf_queues_row_for_dispatcher(client, fake_supabase, monkeypatch):
    upload_mock = MagicMock(return_value="gs://bucket/user-1/proj-1/abc/original.pdf")
    monkeypatch.setattr(files_router, "upload_pdf_bytes", upload_mock)

    ensure_mock = MagicMock(return_value="projects/x/locations/eu/ragCorpora/c")
    monkeypatch.setattr(files_router, "ensure_corpus_for_project", ensure_mock)

    pdf_bytes = b"%PDF-1.4\n%minimal\n%%EOF"
    res = client.post(
        "/api/projects/proj-1/files",
        files={"file": ("smoke.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "queued"
    assert body["filename"] == "smoke.pdf"

    # GCS upload was called with the PDF bytes
    args = upload_mock.call_args.args
    assert args[0] == "user-1"
    assert args[1] == "proj-1"
    assert args[3] == pdf_bytes

    # Corpus was ensured at upload time so the dispatcher has a target.
    ensure_mock.assert_called_once_with("proj-1")

    # The persisted row is queued (no LRO yet — dispatcher will set it).
    row_inserts = [p for tname, p in fake_supabase["inserts"] if tname == "project_files"]
    assert len(row_inserts) == 1
    payload = row_inserts[0]
    assert payload["status"] == "queued"
    assert "ingest_lro_name" not in payload
    assert payload["gcs_blob_path"].startswith("gs://bucket/user-1/proj-1/")
    assert payload["gcs_blob_path"].endswith("/original.pdf")
    assert payload["mime_type"] == "application/pdf"

    # No ingest_jobs row written under the new pipeline
    assert all(tname != "ingest_jobs" for tname, _ in fake_supabase["inserts"])


def test_upload_rejects_unknown_extension(client, fake_supabase, monkeypatch):
    res = client.post(
        "/api/projects/proj-1/files",
        files={"file": ("foo.bin", io.BytesIO(b"\x00\x01"), "application/octet-stream")},
    )
    assert res.status_code == 415
