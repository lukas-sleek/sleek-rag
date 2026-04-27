"""Async ingestion worker.

A single-task event loop, started from FastAPI's lifespan. Picks up the
oldest queued ingest_jobs row, runs Document AI Layout Parser on the file,
embeds chunks via Gemini, and writes document_chunks + chunk_images.
"""
from __future__ import annotations

import asyncio
import logging
import re

from google.cloud import documentai_v1beta3 as documentai
from google.cloud import storage as gcs
from google.oauth2 import service_account
from langsmith import traceable

from app.config import settings
from app.db import supabase
from app.documentai_client import documentai_client, processor_name
from app.gemini_client import gemini_client

log = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 3
MAX_DOCAI_WAIT_SEC = 30 * 60  # 30 min hard cap
EMBED_BATCH_SIZE = 100

_gcs_client: gcs.Client | None = None


def _gcs() -> gcs.Client:
    global _gcs_client
    if _gcs_client is None:
        creds = service_account.Credentials.from_service_account_file(
            settings.gcp_service_account_json_path
        )
        _gcs_client = gcs.Client(project=settings.gcp_project_id, credentials=creds)
    return _gcs_client


async def run_worker() -> None:
    """Main loop: pick next queued job, run it. One job at a time (v1)."""
    log.info("ingest worker started")
    while True:
        try:
            job = _claim_next_job()
            if job is None:
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue
            await asyncio.to_thread(_process_job, job)
        except asyncio.CancelledError:
            log.info("ingest worker cancelled")
            raise
        except Exception as exc:
            log.exception("worker tick failed: %s", exc)
            await asyncio.sleep(POLL_INTERVAL_SEC)


def _claim_next_job() -> dict | None:
    res = supabase().rpc("claim_next_ingest_job").execute()
    rows = res.data or []
    return rows[0] if rows else None


@traceable(run_type="chain", name="ingest.process_job")
def _process_job(job: dict) -> None:
    file_id = job["file_id"]
    job_id = job["id"]
    try:
        gcs_in = _stage_to_gcs(job)
        supabase().table("ingest_jobs").update({"gcs_input_uri": gcs_in}).eq(
            "id", job_id
        ).execute()

        document = _run_layout_parser(gcs_in)
        _set_status(file_id, "embedding")
        _patch_file(file_id, page_count=len(document.pages))

        chunk_count = _persist_chunks(job, document)
        _patch_file(file_id, chunk_count=chunk_count)

        _set_status(file_id, "ready")
        supabase().table("ingest_jobs").update(
            {"state": "done", "finished_at": "now()"}
        ).eq("id", job_id).execute()

        _delete_gcs(gcs_in)
    except Exception as exc:
        log.exception("ingest failed for file %s", file_id)
        msg = str(exc)[:500]
        supabase().table("project_files").update(
            {"status": "failed", "ingest_error": msg}
        ).eq("id", file_id).execute()
        supabase().table("ingest_jobs").update(
            {"state": "failed", "last_error": msg, "finished_at": "now()"}
        ).eq("id", job_id).execute()


# ---------- sub-functions ----------


def _stage_to_gcs(job: dict) -> str:
    file_row = (
        supabase()
        .table("project_files")
        .select("gcs_blob_path,filename,mime_type")
        .eq("id", job["file_id"])
        .single()
        .execute()
        .data
    )
    blob_bytes = supabase().storage.from_("project-files").download(
        file_row["gcs_blob_path"]
    )
    bucket = _gcs().bucket(settings.gcs_staging_bucket)
    obj_name = f"ingest/{job['id']}/{file_row['filename']}"
    blob = bucket.blob(obj_name)
    blob.upload_from_string(
        blob_bytes,
        content_type=file_row.get("mime_type") or "application/pdf",
    )
    return f"gs://{settings.gcs_staging_bucket}/{obj_name}"


def _layout_options() -> documentai.ProcessOptions:
    return documentai.ProcessOptions(
        layout_config=documentai.ProcessOptions.LayoutConfig(
            chunking_config=documentai.ProcessOptions.LayoutConfig.ChunkingConfig(
                chunk_size=500,
                include_ancestor_headings=True,
            ),
            return_images=True,
            return_bounding_boxes=True,
            enable_image_annotation=True,
            enable_image_extraction=True,
            enable_table_annotation=True,
        )
    )


@traceable(run_type="retriever", name="documentai.layout_parser")
def _run_layout_parser(gcs_input_uri: str) -> documentai.Document:
    """Try synchronous process_document first; fall back to batch on size error."""
    bucket_name, obj_name = gcs_input_uri.replace("gs://", "").split("/", 1)
    raw_bytes = _gcs().bucket(bucket_name).blob(obj_name).download_as_bytes()

    try:
        resp = documentai_client().process_document(
            request=documentai.ProcessRequest(
                name=processor_name(),
                raw_document=documentai.RawDocument(
                    content=raw_bytes, mime_type="application/pdf"
                ),
                process_options=_layout_options(),
            )
        )
        return resp.document
    except Exception as e:
        msg = str(e).lower()
        if "exceeds" in msg or "too large" in msg or "page limit" in msg or "15 pages" in msg:
            return _run_batch(gcs_input_uri)
        raise


def _run_batch(gcs_input_uri: str) -> documentai.Document:
    job_dir = gcs_input_uri.split("/")[-2]
    output_uri = f"gs://{settings.gcs_staging_bucket}/output/{job_dir}/"
    op = documentai_client().batch_process_documents(
        request=documentai.BatchProcessRequest(
            name=processor_name(),
            input_documents=documentai.BatchDocumentsInputConfig(
                gcs_documents=documentai.GcsDocuments(
                    documents=[
                        documentai.GcsDocument(
                            gcs_uri=gcs_input_uri, mime_type="application/pdf"
                        )
                    ]
                )
            ),
            document_output_config=documentai.DocumentOutputConfig(
                gcs_output_config=documentai.DocumentOutputConfig.GcsOutputConfig(
                    gcs_uri=output_uri
                )
            ),
            process_options=_layout_options(),
        )
    )
    op.result(timeout=MAX_DOCAI_WAIT_SEC)

    bucket_name, prefix = output_uri.replace("gs://", "").split("/", 1)
    for blob in _gcs().bucket(bucket_name).list_blobs(prefix=prefix):
        if blob.name.endswith(".json"):
            return documentai.Document.from_json(blob.download_as_bytes())
    raise RuntimeError("batch parse produced no output JSON")


_FIGURE_LABEL_RE = re.compile(
    r"^\s*(Figure|Abbildung|Fig\.|Abb\.)\s*([\d.]+)", re.IGNORECASE
)


def _extract_figure_label(text: str | None) -> str | None:
    if not text:
        return None
    m = _FIGURE_LABEL_RE.match(text)
    if not m:
        return None
    return f"{m.group(1).rstrip('.').title()} {m.group(2)}"


def _block_type(chunk) -> str:
    """Map Layout Parser chunk to our block_type enum."""
    if any(cf.image_chunk_field for cf in chunk.chunk_fields):
        return "figure"
    if any(cf.table_chunk_field for cf in chunk.chunk_fields):
        return "table"
    return "paragraph"


def _persist_chunks(job: dict, document: documentai.Document) -> int:
    file_id = job["file_id"]
    user_id = job["user_id"]
    project_id = (
        supabase()
        .table("project_files")
        .select("project_id")
        .eq("id", file_id)
        .single()
        .execute()
        .data["project_id"]
    )

    chunks = list(document.chunked_document.chunks)
    if not chunks:
        return 0

    blob_assets: dict[str, documentai.Document.BlobAsset] = {
        ba.asset_id: ba for ba in document.blob_assets
    }

    texts = [c.content for c in chunks]
    embeddings: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        resp = gemini_client().embeddings.create(
            model=settings.gemini_embedding_model,
            input=batch,
            dimensions=settings.gemini_embedding_dim,
        )
        embeddings.extend(d.embedding for d in resp.data)

    chunk_rows = []
    figure_refs: list[tuple[int, str]] = []  # (chunk_index, blob_asset_id)
    for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        block_type = _block_type(chunk)
        if chunk.page_span:
            page_start = chunk.page_span.page_start or 1
            page_end = chunk.page_span.page_end or page_start
        else:
            page_start = page_end = 1
        heading_path = (
            [h.text for h in chunk.page_headers if h.text]
            if chunk.page_headers
            else None
        ) or None
        figure_label = _extract_figure_label(chunk.content)
        chunk_rows.append(
            {
                "file_id": file_id,
                "project_id": project_id,
                "user_id": user_id,
                "chunk_index": idx,
                "block_type": block_type,
                "content": chunk.content or "",
                "page_start": page_start,
                "page_end": page_end,
                "heading_path": heading_path,
                "figure_label": figure_label,
                "embedding": emb,
            }
        )
        if block_type == "figure":
            for cf in chunk.chunk_fields:
                if cf.image_chunk_field and cf.image_chunk_field.blob_asset_id:
                    figure_refs.append((idx, cf.image_chunk_field.blob_asset_id))
                    break

    inserted = supabase().table("document_chunks").insert(chunk_rows).execute()
    inserted.data.sort(key=lambda r: r["chunk_index"])
    inserted_ids = [r["id"] for r in inserted.data]

    for chunk_idx, asset_id in figure_refs:
        ba = blob_assets.get(asset_id)
        if not ba or not ba.content:
            continue
        chunk_id = inserted_ids[chunk_idx]
        ext = "png"
        mime = ba.mime_type or "image/png"
        if "jpeg" in mime or "jpg" in mime:
            ext = "jpg"
        path = f"{user_id}/{file_id}/{chunk_id}.{ext}"
        try:
            supabase().storage.from_("chunk-images").upload(
                path, bytes(ba.content), {"content-type": mime}
            )
        except Exception as exc:
            log.warning("chunk image upload failed for %s: %s", chunk_id, exc)
            continue
        supabase().table("chunk_images").insert(
            {
                "chunk_id": chunk_id,
                "user_id": user_id,
                "storage_path": path,
                "caption": chunks[chunk_idx].content or None,
                "byte_size": len(ba.content),
            }
        ).execute()

    return len(chunk_rows)


def _set_status(file_id: str, status: str) -> None:
    supabase().table("project_files").update({"status": status}).eq(
        "id", file_id
    ).execute()


def _patch_file(file_id: str, **fields) -> None:
    supabase().table("project_files").update(fields).eq("id", file_id).execute()


def _delete_gcs(uri: str) -> None:
    bucket_name, obj_name = uri.replace("gs://", "").split("/", 1)
    try:
        _gcs().bucket(bucket_name).blob(obj_name).delete()
    except Exception as exc:
        log.warning("staging cleanup failed for %s: %s", uri, exc)
