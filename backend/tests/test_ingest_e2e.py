"""End-to-end ingestion smoke test — runs against real GCP and Supabase.

Gated on RUN_GCP_INTEGRATION=1 because every run costs a few cents.
"""
import os
import time
import uuid

import pytest

from app.db import supabase
from app.workers.ingest import _process_job

PDF_PATH = "/home/lukasthomas/Downloads/somatosensory.pdf"


@pytest.mark.skipif(
    not os.getenv("RUN_GCP_INTEGRATION"), reason="costs money — set RUN_GCP_INTEGRATION=1"
)
def test_e2e_pdf_ingest():
    user = next(
        u for u in supabase().auth.admin.list_users() if u.email == "test@test.com"
    )
    user_id = user.id

    proj = (
        supabase()
        .table("projects")
        .insert({"user_id": user_id, "name": f"e2e-{uuid.uuid4().hex[:8]}"})
        .execute()
    )
    project_id = proj.data[0]["id"]

    file_id = None
    blob_path = None
    try:
        with open(PDF_PATH, "rb") as f:
            pdf_bytes = f.read()
        filename = f"e2e-{uuid.uuid4().hex[:8]}.pdf"

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

        # Claim and run job synchronously (skip the worker loop).
        claim = supabase().rpc("claim_next_ingest_job").execute()
        job = next(j for j in claim.data if j["id"] == job_id)
        t0 = time.time()
        _process_job(job)
        elapsed = time.time() - t0

        row = (
            supabase()
            .table("project_files")
            .select("status,chunk_count,page_count,ingest_error")
            .eq("id", file_id)
            .single()
            .execute()
            .data
        )
        assert row["status"] == "ready", f"status={row['status']} err={row.get('ingest_error')}"
        assert row["chunk_count"] > 0
        assert row["page_count"] >= 1

        chunks = (
            supabase()
            .table("document_chunks")
            .select("id,page_start,page_end,embedding,block_type")
            .eq("file_id", file_id)
            .execute()
            .data
        )
        assert len(chunks) == row["chunk_count"]
        assert all(c["page_start"] >= 1 for c in chunks)
        first_emb = chunks[0]["embedding"]
        if isinstance(first_emb, str):
            # pgvector serializes as string; just check it parses to a list.
            import json

            first_emb = json.loads(first_emb)
        assert len(first_emb) == 768

        print(
            f"E2E OK: {row['page_count']}p / {row['chunk_count']}c in {elapsed:.1f}s"
        )
    finally:
        if file_id:
            supabase().table("project_files").delete().eq("id", file_id).execute()
        if blob_path:
            try:
                supabase().storage.from_("project-files").remove([blob_path])
            except Exception:
                pass
        supabase().table("projects").delete().eq("id", project_id).execute()
