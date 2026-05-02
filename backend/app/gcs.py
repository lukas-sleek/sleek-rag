"""Thin wrapper around google-cloud-storage scoped to the canonical files bucket.

Plan 18.2 T3. The bucket layout is fixed by the master spec (Q5 + 18.1 T2):
    gs://{GCS_FILES_BUCKET}/{user_id}/{project_id}/{file_id}/{sanitized_filename}.pdf

We embed the original (sanitized) filename in the object key so the Vertex RAG
display_name (which the SDK derives from the GCS basename) is human-readable
in the GCP console and Vertex Studio.
"""
from __future__ import annotations

import re
import unicodedata

from google.cloud import storage
from google.oauth2 import service_account

from app.config import settings

_client: storage.Client | None = None


def storage_client() -> storage.Client:
    global _client
    if _client is None:
        creds = None
        if settings.gcp_service_account_json_path:
            creds = service_account.Credentials.from_service_account_file(
                settings.gcp_service_account_json_path
            )
        _client = storage.Client(project=settings.gcp_project_id, credentials=creds)
    return _client


def sanitize_filename(filename: str | None) -> str:
    """ASCII-safe, slash-free, .pdf-suffixed object basename.

    GCS allows non-ASCII keys but Vertex RAG's display_name parser is brittle
    around them; we strip diacritics and collapse anything non-alphanum.
    Always returns a name ending in .pdf.
    """
    name = (filename or "original.pdf").rsplit(".", 1)[0]
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_only = nfkd.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_only).strip("._-")
    if not cleaned:
        cleaned = "original"
    return f"{cleaned[:120]}.pdf"


def object_key(user_id: str, project_id: str, file_id: str, filename: str | None = None) -> str:
    basename = sanitize_filename(filename)
    return f"{user_id}/{project_id}/{file_id}/{basename}"


def gcs_uri(key: str) -> str:
    return f"gs://{settings.gcs_files_bucket}/{key}"


def upload_pdf_bytes(
    user_id: str, project_id: str, file_id: str, data: bytes, filename: str | None = None
) -> str:
    """Upload canonical PDF bytes; return the full gs:// URI."""
    key = object_key(user_id, project_id, file_id, filename)
    blob = storage_client().bucket(settings.gcs_files_bucket).blob(key)
    blob.upload_from_string(data, content_type="application/pdf")
    return gcs_uri(key)


def delete_prefix(prefix: str) -> int:
    """Delete every object under a prefix. Returns the count deleted."""
    bucket = storage_client().bucket(settings.gcs_files_bucket)
    blobs = list(bucket.list_blobs(prefix=prefix))
    for b in blobs:
        try:
            b.delete()
        except Exception:
            pass
    return len(blobs)
