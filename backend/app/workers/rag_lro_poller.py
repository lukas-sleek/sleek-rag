"""Drives rag.import_files LROs end-to-end.

Two responsibilities, both run on every tick:

1. Dispatcher — for each project with rows in status='queued' AND no
   in-flight LRO on its corpus, batch every queued GCS URI into a single
   rag.import_files_async call. Vertex serialises operations per corpus,
   so concurrent uploads MUST share an LRO instead of each firing one.

2. Resolver — for each row in status='parsing', poll the LRO and flip
   the status (ready / failed) when it resolves. When several rows share
   one LRO, the resolver maps per-file partialFailures back to the right
   row by GCS URI.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from google.cloud.aiplatform_v1beta1.services.vertex_rag_data_service import (
    VertexRagDataServiceClient,
)
from google.longrunning.operations_pb2 import GetOperationRequest
from google.oauth2 import service_account
from vertexai.preview import rag

from app.config import settings
from app.db import supabase
from app.rag_corpus import import_pdfs

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


def _proto_any_to_dict(any_msg) -> dict:
    """Best-effort decode of a google.protobuf.Any holding a known JSON-able type."""
    try:
        from google.protobuf.json_format import MessageToDict
        # Any messages can be unpacked when their type_url is known. For
        # response/metadata we only need the JSON-like field map, so use
        # MessageToDict on the wrapper which yields {'@type': ..., ...fields}.
        return MessageToDict(any_msg, preserving_proto_field_name=False)
    except Exception:
        return {}


def _row_corpus(row: dict) -> str | None:
    return row.get("rag_corpus_name") or (row.get("projects") or {}).get("rag_corpus_name")


def _partial_failure_uris(meta: dict) -> set[str]:
    """Extract GCS URIs from partialFailures messages.

    Vertex puts the offending URI into the failure message body
    (e.g. "...processing gs://bucket/path/file.pdf"). We pattern-match it
    out so multi-file LROs can mark only the failed rows as failed.
    """
    uris: set[str] = set()
    pfs = (meta.get("genericMetadata") or {}).get("partialFailures") or []
    for f in pfs:
        msg = f.get("message") or ""
        for token in msg.split():
            if token.startswith("gs://"):
                # strip trailing punctuation a Vertex message might tack on
                uris.add(token.rstrip(".,)\""))
    return uris


def _resolve_lro(op_name: str, rows_for_op: list[dict]) -> None:
    """Resolve one LRO and update every row that shares its operation name."""
    try:
        op = _ops_client().get_operation(GetOperationRequest(name=op_name))
    except Exception:
        log.exception("get_operation failed for op=%s", op_name)
        return

    if not op.done:
        return

    # Hard error: every row in the batch fails with the same message.
    if op.HasField("error") and op.error.code != 0:
        msg = (op.error.message or "import failed")[:500]
        for r in rows_for_op:
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

    failed_uris = _partial_failure_uris(metadata_payload)
    pfs = (metadata_payload.get("genericMetadata") or {}).get("partialFailures") or []
    fallback_msg = (
        (pfs[0].get("message") or "import failed")[:500]
        if pfs
        else f"import failed: {failed_count} failed, {imported_count} imported"
    )

    # If the LRO yielded zero imports AND we couldn't resolve which URIs failed,
    # mark every row in the batch as failed — better than stranding them as
    # parsing forever.
    if imported_count == 0 and failed_count > 0 and not failed_uris:
        for r in rows_for_op:
            supabase().table("project_files").update({
                "status": "failed",
                "ingest_error": fallback_msg,
                "ingest_lro_name": None,
            }).eq("id", r["id"]).execute()
        log.warning("LRO %s failed (all rows): %s", op_name, fallback_msg)
        return

    for r in rows_for_op:
        gcs_uri = r.get("gcs_blob_path") or ""
        corpus_name = _row_corpus(r)
        if gcs_uri in failed_uris:
            # Find the specific message for this URI, fall back to the first.
            specific = next(
                (f.get("message") for f in pfs if f.get("message") and gcs_uri in f["message"]),
                fallback_msg,
            )
            supabase().table("project_files").update({
                "status": "failed",
                "ingest_error": (specific or fallback_msg)[:500],
                "ingest_lro_name": None,
            }).eq("id", r["id"]).execute()
            log.warning("file %s ingest failed: %s", r["id"], specific)
            continue

        rag_file_name = (
            _resolve_rag_file_name(corpus_name, gcs_uri)
            if corpus_name and gcs_uri
            else None
        )
        update = {"status": "ready", "ingest_lro_name": None}
        if rag_file_name:
            update["rag_file_name"] = rag_file_name
        supabase().table("project_files").update(update).eq("id", r["id"]).execute()
        log.info("file %s ingest ready: %s", r["id"], rag_file_name)


def _claim_in_flight_rows() -> list[dict]:
    res = (
        supabase()
        .table("project_files")
        .select("id,ingest_lro_name,gcs_blob_path,project_id,projects(rag_corpus_name)")
        .eq("status", "parsing")
        .not_.is_("ingest_lro_name", "null")
        .execute()
    )
    return res.data or []


def _claim_queued_rows() -> list[dict]:
    res = (
        supabase()
        .table("project_files")
        .select("id,gcs_blob_path,project_id,projects(rag_corpus_name)")
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
    """Batch queued rows into one LRO per corpus, skipping corpora with in-flight imports."""
    queued = await asyncio.to_thread(_claim_queued_rows)
    if not queued:
        return

    busy_corpora = await asyncio.to_thread(_corpora_with_in_flight_imports)

    by_corpus: dict[str, list[dict]] = defaultdict(list)
    for r in queued:
        corpus = _row_corpus(r)
        if not corpus or corpus in busy_corpora:
            continue
        by_corpus[corpus].append(r)

    for corpus_name, rows in by_corpus.items():
        uris = [r["gcs_blob_path"] for r in rows if r.get("gcs_blob_path")]
        if not uris:
            continue
        try:
            op_name = await import_pdfs(corpus_name, uris)
        except Exception:
            log.exception("dispatch failed for corpus %s", corpus_name)
            continue
        for r in rows:
            supabase().table("project_files").update({
                "status": "parsing",
                "ingest_lro_name": op_name,
            }).eq("id", r["id"]).execute()
        log.info("dispatched %d file(s) on corpus %s as %s", len(rows), corpus_name, op_name)


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
    """Main loop: every POLL_INTERVAL_SEC, dispatch queued + resolve in-flight."""
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
