"""Polls outstanding rag.import_files LROs and updates project_files status.

Plan 18.2 T5. Replaces the old Document AI ingest worker. The upload endpoint
fires rag.import_files_async and persists the LRO name on the row; this
worker resolves those operations and flips parsing -> ready / failed.
"""
from __future__ import annotations

import asyncio
import logging

from google.cloud.aiplatform_v1beta1.services.vertex_rag_data_service import (
    VertexRagDataServiceClient,
)
from google.longrunning.operations_pb2 import GetOperationRequest
from google.oauth2 import service_account
from vertexai.preview import rag

from app.config import settings
from app.db import supabase

log = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 15

_client: VertexRagDataServiceClient | None = None


def _ops_client() -> VertexRagDataServiceClient:
    """Regional Vertex RAG client used to resolve operation names. Cached."""
    global _client
    if _client is None:
        creds = None
        if settings.gcp_service_account_json_path:
            creds = service_account.Credentials.from_service_account_file(
                settings.gcp_service_account_json_path
            )
        endpoint = f"{settings.gcp_location}-aiplatform.googleapis.com"
        _client = VertexRagDataServiceClient(
            credentials=creds,
            client_options={"api_endpoint": endpoint},
        )
    return _client


def _resolve_rag_file_name(corpus_name: str, gcs_uri: str) -> str | None:
    """After a successful import LRO, find the RagFile resource matching gcs_uri.

    The SDK names imported files after the source URI; we do a simple
    display_name match. None if not found (treated as a soft warning).
    """
    try:
        for f in rag.list_files(corpus_name):
            if f.display_name and (f.display_name == gcs_uri or gcs_uri.endswith(f.display_name)):
                return f.name
        # Fallback: if the corpus has exactly one file post-import (single-shot
        # uploads), trust it. Many display_name conventions exist depending on
        # SDK version.
        files = list(rag.list_files(corpus_name))
        if len(files) == 1:
            return files[0].name
    except Exception:
        log.exception("list_files failed for corpus %s", corpus_name)
    return None


def _poll_one(row: dict) -> None:
    op_name = row.get("ingest_lro_name")
    file_id = row["id"]
    corpus_name = row.get("rag_corpus_name") or (row.get("projects") or {}).get("rag_corpus_name")
    gcs_uri = row.get("gcs_blob_path")

    try:
        op = _ops_client().get_operation(GetOperationRequest(name=op_name))
    except Exception as exc:
        log.exception("get_operation failed for file %s op=%s: %s", file_id, op_name, exc)
        return

    if not op.done:
        return

    if op.HasField("error") and op.error.code != 0:
        msg = (op.error.message or "import failed")[:500]
        supabase().table("project_files").update({
            "status": "failed",
            "ingest_error": msg,
            "ingest_lro_name": None,
        }).eq("id", file_id).execute()
        log.warning("file %s ingest failed: %s", file_id, msg)
        return

    rag_file_name = (
        _resolve_rag_file_name(corpus_name, gcs_uri) if corpus_name and gcs_uri else None
    )
    update = {"status": "ready", "ingest_lro_name": None}
    if rag_file_name:
        update["rag_file_name"] = rag_file_name
    supabase().table("project_files").update(update).eq("id", file_id).execute()
    log.info("file %s ingest ready: %s", file_id, rag_file_name)


def _claim_pending_rows() -> list[dict]:
    """Rows currently being ingested. Joins projects to get the corpus name."""
    res = (
        supabase()
        .table("project_files")
        .select("id,ingest_lro_name,gcs_blob_path,project_id,projects(rag_corpus_name)")
        .eq("status", "parsing")
        .not_.is_("ingest_lro_name", "null")
        .execute()
    )
    return res.data or []


async def run_poller() -> None:
    """Main loop: every POLL_INTERVAL_SEC, resolve every in-flight LRO."""
    log.info("rag LRO poller started (interval=%ss)", POLL_INTERVAL_SEC)
    while True:
        try:
            rows = await asyncio.to_thread(_claim_pending_rows)
            if rows:
                await asyncio.gather(
                    *(asyncio.to_thread(_poll_one, r) for r in rows)
                )
        except asyncio.CancelledError:
            log.info("rag LRO poller cancelled")
            raise
        except Exception:
            log.exception("poller tick failed")
        await asyncio.sleep(POLL_INTERVAL_SEC)
