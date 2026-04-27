"""Smoke test for the new file-upload path: row insert, Storage upload,
ingest_jobs enqueue. Uses the service-role client (bypasses RLS) so we
can self-clean after."""
import io
import uuid

import pytest

from app.db import supabase


@pytest.fixture
def test_project():
    """Create a temp project owned by the seeded test user."""
    user_res = (
        supabase().auth.admin.list_users()
    )
    user = next(u for u in user_res if u.email == "test@test.com")
    user_id = user.id

    proj = (
        supabase()
        .table("projects")
        .insert({"user_id": user_id, "name": f"smoke-{uuid.uuid4().hex[:8]}"})
        .execute()
    )
    project_id = proj.data[0]["id"]
    yield user_id, project_id
    supabase().table("projects").delete().eq("id", project_id).execute()


def test_upload_creates_storage_blob_and_job(test_project):
    user_id, project_id = test_project
    pdf_bytes = b"%PDF-1.4\n%fake\n%%EOF"
    filename = f"smoke-{uuid.uuid4().hex[:8]}.pdf"

    insert = (
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
    file_id = insert.data[0]["id"]

    blob_path = f"{user_id}/{file_id}/{filename}"
    try:
        supabase().storage.from_("project-files").upload(
            blob_path, pdf_bytes, {"content-type": "application/pdf"}
        )
        supabase().table("project_files").update(
            {"gcs_blob_path": blob_path, "status": "parsing"}
        ).eq("id", file_id).execute()
        supabase().table("ingest_jobs").insert(
            {"file_id": file_id, "user_id": user_id, "state": "queued"}
        ).execute()

        row = (
            supabase()
            .table("project_files")
            .select("status,gcs_blob_path")
            .eq("id", file_id)
            .single()
            .execute()
            .data
        )
        assert row["status"] == "parsing"
        assert row["gcs_blob_path"] == blob_path

        job = (
            supabase()
            .table("ingest_jobs")
            .select("state")
            .eq("file_id", file_id)
            .single()
            .execute()
            .data
        )
        assert job["state"] == "queued"
    finally:
        try:
            supabase().storage.from_("project-files").remove([blob_path])
        except Exception:
            pass
        supabase().table("project_files").delete().eq("id", file_id).execute()
