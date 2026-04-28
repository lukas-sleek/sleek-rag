"""Retrieval integration tests.

Plan 14 deleted the regex pattern router; the agentic `search_chunks` tool
now drives retrieval based on structured filters from the model. Pattern-
detection tests are obsolete. The end-to-end roundtrip below ingests a
real PDF and embeds via Gemini, so it stays gated on
RUN_GCP_INTEGRATION=1.
"""
import os
import time
import uuid

import pytest

from app.db import supabase
from app.retrieval import _attach_images, _by_vector
from app.workers.ingest import _process_job

PDF_PATH = "/home/lukasthomas/Downloads/somatosensory.pdf"


def _retrieve_vector(query: str, project_id: str, top_k: int = 8):
    return _attach_images(_by_vector(query, project_id, top_k, None))


# --- integration roundtrip ---


@pytest.fixture
def seeded_project():
    """Ingest a real PDF end-to-end so retrieval can run against it.

    Reuses the e2e ingest path. Self-cleans after the test.
    """
    user = next(
        u for u in supabase().auth.admin.list_users() if u.email == "test@test.com"
    )
    user_id = user.id
    proj = (
        supabase()
        .table("projects")
        .insert({"user_id": user_id, "name": f"retr-{uuid.uuid4().hex[:8]}"})
        .execute()
    )
    project_id = proj.data[0]["id"]

    file_id = None
    blob_path = None
    try:
        with open(PDF_PATH, "rb") as f:
            pdf_bytes = f.read()
        filename = f"retr-{uuid.uuid4().hex[:8]}.pdf"
        ins = (
            supabase()
            .table("project_files")
            .insert(
                {
                    "project_id": project_id,
                    "user_id": user_id,
                    "filename": filename,
                    "size_bytes": len(pdf_bytes),
                    "mime_type": "application/pdf",
                    "status": "uploading",
                }
            )
            .execute()
        )
        file_id = ins.data[0]["id"]
        blob_path = f"{user_id}/{file_id}/{filename}"
        supabase().storage.from_("project-files").upload(
            blob_path, pdf_bytes, {"content-type": "application/pdf"}
        )
        supabase().table("project_files").update(
            {"gcs_blob_path": blob_path, "status": "parsing"}
        ).eq("id", file_id).execute()
        job_ins = (
            supabase()
            .table("ingest_jobs")
            .insert({"file_id": file_id, "user_id": user_id, "state": "queued"})
            .execute()
        )
        job_id = job_ins.data[0]["id"]
        claim = supabase().rpc("claim_next_ingest_job").execute()
        job = next(j for j in claim.data if j["id"] == job_id)
        t0 = time.time()
        _process_job(job)
        elapsed = time.time() - t0
        print(f"[seeded_project] ingested in {elapsed:.1f}s")
        yield {"project_id": project_id, "user_id": user_id, "file_id": file_id}
    finally:
        if file_id:
            supabase().table("project_files").delete().eq("id", file_id).execute()
        if blob_path:
            try:
                supabase().storage.from_("project-files").remove([blob_path])
            except Exception:
                pass
        supabase().table("projects").delete().eq("id", project_id).execute()


@pytest.mark.skipif(
    not os.getenv("RUN_GCP_INTEGRATION"),
    reason="costs money — set RUN_GCP_INTEGRATION=1",
)
def test_vector_default_returns_chunks(seeded_project):
    chunks = _retrieve_vector(
        "somatosensory system", seeded_project["project_id"]
    )
    assert len(chunks) > 0
    assert all(0.0 <= c.score <= 1.0 for c in chunks)
    assert all(c.project_id == seeded_project["project_id"] for c in chunks)
