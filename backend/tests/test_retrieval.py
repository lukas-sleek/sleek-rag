"""Retrieval pattern + integration tests.

Pattern-detection tests run unconditionally. The full retrieval roundtrip is
gated on RUN_GCP_INTEGRATION=1 because it ingests a real PDF and embeds via
Gemini.
"""
import os
import time
import uuid

import pytest

from app.db import supabase
from app.retrieval import (
    _FIGURE_RE,
    _PAGE_RE,
    _SECTION_RE,
    _VISUAL_RE,
    _normalize_figure_label,
    retrieve,
)
from app.workers.ingest import _process_job

PDF_PATH = "/home/lukasthomas/Downloads/somatosensory.pdf"


# --- pattern detection ---


def test_page_pattern_matches_english():
    m = _PAGE_RE.search("Show me everything on page 17")
    assert m and m.group(1) == "17"


def test_page_pattern_matches_german():
    m = _PAGE_RE.search("Was steht auf Seite 4?")
    assert m and m.group(1) == "4"


def test_figure_pattern_matches_label():
    m = _FIGURE_RE.search("What's in Figure 3.6?")
    assert m
    assert _normalize_figure_label(m.group(1), m.group(2)) == "Figure 3.6"


def test_figure_pattern_matches_german_short():
    m = _FIGURE_RE.search("Was zeigt Abb. 5?")
    assert m
    assert _normalize_figure_label(m.group(1), m.group(2)) == "Abb 5"


def test_section_pattern_matches():
    m = _SECTION_RE.search("Erkläre Abschnitt 3.6")
    assert m and m.group(1) == "3.6"


def test_visual_pattern_detects_drawing():
    assert _VISUAL_RE.search("show me the technical drawing")
    assert _VISUAL_RE.search("zeig mir die Zeichnung")
    assert not _VISUAL_RE.search("summarize the contract terms")


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
    chunks = retrieve(
        query="somatosensory system",
        project_id=seeded_project["project_id"],
        user_id=seeded_project["user_id"],
    )
    assert len(chunks) > 0
    assert all(0.0 <= c.score <= 1.0 for c in chunks)
    assert all(c.project_id == seeded_project["project_id"] for c in chunks)


@pytest.mark.skipif(
    not os.getenv("RUN_GCP_INTEGRATION"),
    reason="costs money — set RUN_GCP_INTEGRATION=1",
)
def test_page_filter(seeded_project):
    chunks = retrieve(
        query="Show me everything on page 1",
        project_id=seeded_project["project_id"],
        user_id=seeded_project["user_id"],
    )
    assert len(chunks) > 0
    assert all(c.page_start <= 1 <= c.page_end for c in chunks)


@pytest.mark.skipif(
    not os.getenv("RUN_GCP_INTEGRATION"),
    reason="costs money — set RUN_GCP_INTEGRATION=1",
)
def test_visual_filter_prefers_figures_or_falls_back(seeded_project):
    chunks = retrieve(
        query="show me the technical drawing",
        project_id=seeded_project["project_id"],
        user_id=seeded_project["user_id"],
    )
    # If there are any figures in the seed PDF, all results must be figures.
    # If not, the vector-search fallback returns at least one chunk.
    assert len(chunks) > 0
    figure_chunks = [c for c in chunks if c.block_type == "figure"]
    if figure_chunks:
        assert all(c.block_type == "figure" for c in chunks)
