"""Reset a project's failed Vertex RAG state so it can be re-uploaded.

Usage:
    backend/venv/bin/python scripts/reset_failed_uploads.py <project_id>

Does:
  1. Lists every Vertex RAG corpus in the region whose displayName matches
     `sleek-rag-<project_id>` and deletes them all (including the orphans
     created by the race condition before plan 18.2.1).
  2. Clears projects.rag_corpus_name for that project so the next upload
     lazy-creates a fresh corpus.
  3. Deletes every project_files row for that project (and its GCS prefix)
     so the user can re-upload from a clean slate.

Safe to run multiple times. Aborts if the project doesn't exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make backend.app imports work when run from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import vertexai
from google.oauth2 import service_account
from vertexai.preview import rag

from app.config import settings
from app.db import supabase
from app.gcs import delete_prefix


def _init_vertex() -> None:
    creds = None
    if settings.gcp_service_account_json_path:
        creds = service_account.Credentials.from_service_account_file(
            settings.gcp_service_account_json_path
        )
    vertexai.init(
        project=settings.gcp_project_id,
        location=settings.gcp_location,
        credentials=creds,
    )


def main(project_id: str) -> int:
    _init_vertex()

    proj = (
        supabase()
        .table("projects")
        .select("id,name,user_id,rag_corpus_name")
        .eq("id", project_id)
        .single()
        .execute()
    ).data
    if not proj:
        print(f"ERROR: project {project_id} not found", file=sys.stderr)
        return 2
    print(f"Project: {proj['name']} (user {proj['user_id']})")

    target_display = f"sleek-rag-{project_id}"
    deleted_corpora = 0
    for c in rag.list_corpora():
        if c.display_name == target_display:
            print(f"  deleting corpus {c.name}")
            try:
                rag.delete_corpus(c.name)
                deleted_corpora += 1
            except Exception as exc:
                print(f"  WARN: delete failed: {exc}", file=sys.stderr)
    print(f"deleted {deleted_corpora} corpora")

    supabase().table("projects").update({"rag_corpus_name": None}).eq(
        "id", project_id
    ).execute()
    print("cleared projects.rag_corpus_name")

    files = (
        supabase()
        .table("project_files")
        .select("id")
        .eq("project_id", project_id)
        .execute()
    ).data or []
    if files:
        supabase().table("project_files").delete().eq("project_id", project_id).execute()
        print(f"deleted {len(files)} project_files rows")

    n = delete_prefix(f"{proj['user_id']}/{project_id}/")
    print(f"deleted {n} GCS objects under {proj['user_id']}/{project_id}/")

    print("Done. Re-upload to test the fixed pipeline.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
