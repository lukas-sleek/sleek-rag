"""Drives Vertex RAG folder-import LROs end-to-end.

Plan 20.0: serverless mode in us-central1. One folder import per
(corpus, project, user) — Vertex recurses the project folder
`gs://{bucket}/{user}/{project}/` and ingests every file.

Two responsibilities, both run on every tick:

1. Dispatcher — for each project with rows in status='queued' AND no
   in-flight LRO on its corpus, fire ONE folder import covering every
   queued file at once. Vertex serialises operations per corpus, so
   concurrent uploads MUST share an LRO.

2. Resolver — for each row in status='parsing':
     * map the row's sanitized filename to a RagFile via list_rag_files
       (display_name match — gcs_source.uris is empty under serverless)
     * when the matching RagFile reports state=ACTIVE, flip the row
       to ready and persist rag_file_name
     * when the LRO completes with a hard error, mark every still-parsing
       row in the batch as failed
     * partial failures are mapped by display_name where possible
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict

from google.cloud.aiplatform_v1beta1.services.vertex_rag_data_service import (
    VertexRagDataServiceClient,
)
from google.cloud.aiplatform_v1beta1.types import ListRagFilesRequest
from google.longrunning.operations_pb2 import GetOperationRequest

from app.config import settings
from app.db import supabase
from app.gcs import sanitize_filename
from app.rag_corpus import _credentials, _init_vertex_for, import_folder

log = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 15

_CORPUS_LOCATION_RE = re.compile(r"projects/[^/]+/locations/([^/]+)/ragCorpora/")
_clients: dict[str, VertexRagDataServiceClient] = {}


def _ops_client(corpus_name: str) -> VertexRagDataServiceClient:
    """Per-region GAPIC client. Cached so legacy EU corpora keep working."""
    m = _CORPUS_LOCATION_RE.match(corpus_name or "")
    location = m.group(1) if m else settings.gcp_location
    client = _clients.get(location)
    if client is None:
        client = VertexRagDataServiceClient(
            credentials=_credentials(),
            client_options={
                "api_endpoint": f"{location}-aiplatform.googleapis.com"
            },
        )
        _clients[location] = client
    return client


def _list_rag_files_by_display_name(corpus_name: str) -> dict[str, dict]:
    """Return {display_name: {rag_file_name, state}} for the corpus.

    Treats NotFound as empty — the corpus may have been deleted in Vertex
    while a stale `projects.rag_corpus_name` still points at it. Logging
    the full traceback every tick was noisy.
    """
    from google.api_core.exceptions import NotFound

    out: dict[str, dict] = {}
    try:
        pager = _ops_client(corpus_name).list_rag_files(
            ListRagFilesRequest(parent=corpus_name)
        )
        for f in pager:
            out[f.display_name] = {
                "rag_file_name": f.name,
                "state": f.file_status.state.name,
            }
    except NotFound:
        log.warning("list_rag_files: corpus %s does not exist (stale row?)", corpus_name)
    except Exception:
        log.exception("list_rag_files failed for corpus %s", corpus_name)
    return out


def _proto_any_to_dict(any_msg) -> dict:
    try:
        from google.protobuf.json_format import MessageToDict
        return MessageToDict(any_msg, preserving_proto_field_name=False)
    except Exception:
        return {}


def _row_corpus(row: dict) -> str | None:
    return row.get("rag_corpus_name") or (row.get("projects") or {}).get("rag_corpus_name")


def _expected_display_name(row: dict) -> str:
    """Match what Vertex assigns when importing from a GCS folder.

    The Layout-Parser-imported RagFile's display_name is the GCS object's
    basename, which is `sanitize_filename(row.filename)` (set at upload time
    by app/gcs.object_key). Keep these in sync.
    """
    return sanitize_filename(row.get("filename") or "")


def _resolve_lro(op_name: str, rows_for_op: list[dict]) -> None:
    """Per-tick reconciliation for one LRO and the rows it covers."""
    corpus_names = {_row_corpus(r) for r in rows_for_op if _row_corpus(r)}
    if not corpus_names:
        return
    # All rows in one LRO share a corpus by construction (dispatcher groups
    # by corpus). Pick any.
    corpus_name = next(iter(corpus_names))

    # 1. Per-file readiness — flip rows whose RagFile is already ACTIVE,
    #    even before the LRO completes.
    files_by_display = _list_rag_files_by_display_name(corpus_name)
    still_parsing: list[dict] = []
    for r in rows_for_op:
        match = files_by_display.get(_expected_display_name(r))
        if match and match["state"] == "ACTIVE":
            supabase().table("project_files").update({
                "status": "ready",
                "ingest_lro_name": None,
                "rag_file_name": match["rag_file_name"],
            }).eq("id", r["id"]).execute()
            log.info("file %s ingest ready: %s", r["id"], match["rag_file_name"])
        else:
            still_parsing.append(r)

    if not still_parsing:
        return

    # 2. LRO terminal-state check for any rows still without an ACTIVE RagFile.
    try:
        op = _ops_client(corpus_name).get_operation(GetOperationRequest(name=op_name))
    except Exception:
        log.exception("get_operation failed for op=%s", op_name)
        return

    if not op.done:
        return

    # Hard error: every still-parsing row in the batch fails with the same message.
    if op.HasField("error") and op.error.code != 0:
        msg = (op.error.message or "import failed")[:500]
        for r in still_parsing:
            supabase().table("project_files").update({
                "status": "failed",
                "ingest_error": msg,
                "ingest_lro_name": None,
            }).eq("id", r["id"]).execute()
        log.warning("LRO %s failed (hard): %s", op_name, msg)
        return

    response_payload = _proto_any_to_dict(op.response) if op.HasField("response") else {}
    metadata_payload = _proto_any_to_dict(op.metadata) if op.HasField("metadata") else {}
    failed_count = int(response_payload.get("failedRagFilesCount", 0) or 0)
    imported_count = int(response_payload.get("importedRagFilesCount", 0) or 0)
    pfs = (metadata_payload.get("genericMetadata") or {}).get("partialFailures") or []
    fallback_msg = (
        (pfs[0].get("message") or "import failed")[:500]
        if pfs
        else f"import failed: {failed_count} failed, {imported_count} imported"
    )

    # If we couldn't match by display_name and the LRO is done, mark
    # leftover rows failed — better than leaving them stuck on parsing.
    for r in still_parsing:
        expected = _expected_display_name(r)
        # Try matching any partial-failure message against the row's
        # display_name (Vertex sometimes embeds it in the failure text).
        specific = None
        for f in pfs:
            msg = (f.get("message") or "")
            if expected and expected in msg:
                specific = msg
                break
        ingest_error = (specific or fallback_msg)[:500]
        supabase().table("project_files").update({
            "status": "failed",
            "ingest_error": ingest_error,
            "ingest_lro_name": None,
        }).eq("id", r["id"]).execute()
        log.warning("file %s ingest failed: %s", r["id"], ingest_error)


def _claim_in_flight_rows() -> list[dict]:
    res = (
        supabase()
        .table("project_files")
        .select(
            "id,ingest_lro_name,filename,user_id,project_id,"
            "projects(rag_corpus_name)"
        )
        .eq("status", "parsing")
        .not_.is_("ingest_lro_name", "null")
        .execute()
    )
    return res.data or []


def _claim_queued_rows() -> list[dict]:
    res = (
        supabase()
        .table("project_files")
        .select(
            "id,filename,user_id,project_id,projects(rag_corpus_name)"
        )
        .eq("status", "queued")
        .execute()
    )
    return res.data or []


def _resolve_step() -> None:
    rows = _claim_in_flight_rows()
    if not rows:
        return
    by_op: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        op_name = r.get("ingest_lro_name")
        if op_name:
            by_op[op_name].append(r)
    for op_name, rows_for_op in by_op.items():
        _resolve_lro(op_name, rows_for_op)


async def _dispatch_step() -> None:
    """Fire one folder-import LRO per (corpus,project,user), skipping busy corpora."""
    queued = await asyncio.to_thread(_claim_queued_rows)
    if not queued:
        return

    busy_corpora = await asyncio.to_thread(_corpora_with_in_flight_imports)

    # Group by (corpus, project_id, user_id) — folder URI is per-project.
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for r in queued:
        corpus = _row_corpus(r)
        if not corpus or corpus in busy_corpora:
            continue
        project_id = r.get("project_id")
        user_id = r.get("user_id")
        if not project_id or not user_id:
            continue
        groups[(corpus, project_id, user_id)].append(r)

    for (corpus_name, project_id, user_id), rows in groups.items():
        folder_uri = (
            f"gs://{settings.gcs_files_bucket}/{user_id}/{project_id}/"
        )
        # Re-init Vertex at the corpus's region before the LRO call.
        await asyncio.to_thread(_init_vertex_for, corpus_name)
        try:
            op_name = await import_folder(corpus_name, folder_uri)
        except Exception:
            log.exception("dispatch failed for corpus %s", corpus_name)
            continue
        for r in rows:
            supabase().table("project_files").update({
                "status": "parsing",
                "ingest_lro_name": op_name,
            }).eq("id", r["id"]).execute()
        log.info(
            "dispatched %d file(s) on corpus %s (folder=%s) as %s",
            len(rows), corpus_name, folder_uri, op_name,
        )


def _corpora_with_in_flight_imports() -> set[str]:
    res = (
        supabase()
        .table("project_files")
        .select("projects(rag_corpus_name)")
        .eq("status", "parsing")
        .not_.is_("ingest_lro_name", "null")
        .execute()
    )
    busy: set[str] = set()
    for r in res.data or []:
        c = (r.get("projects") or {}).get("rag_corpus_name")
        if c:
            busy.add(c)
    return busy


async def run_poller() -> None:
    log.info("rag LRO poller started (interval=%ss)", POLL_INTERVAL_SEC)
    while True:
        try:
            await _dispatch_step()
            await asyncio.to_thread(_resolve_step)
        except asyncio.CancelledError:
            log.info("rag LRO poller cancelled")
            raise
        except Exception:
            log.exception("poller tick failed")
        await asyncio.sleep(POLL_INTERVAL_SEC)
