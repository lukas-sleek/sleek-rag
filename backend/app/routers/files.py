import asyncio
import datetime as _dt
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from langsmith import traceable
from pydantic import BaseModel

from app.auth import current_user_id
from app.db import supabase
from app.gcs import gcs_uri, object_key, storage_client, upload_pdf_bytes
from app.rag_corpus import ensure_corpus_for_project

router = APIRouter(prefix="/api/projects/{project_id}/files", tags=["files"])

# Office formats Document AI's Layout Parser doesn't accept directly.
# Converted to PDF via headless LibreOffice on upload.
_OFFICE_EXTS = {
    "doc", "docx", "docm", "dot", "dotx", "dotm", "rtf", "odt",
    "xls", "xlsx", "xlsm", "xlsb", "xlt", "xltx", "xltm", "ods",
    "ppt", "pptx", "pptm", "pps", "ppsx", "ppsm", "pot", "potx", "potm", "odp",
}


async def _convert_office_to_pdf(data: bytes, ext: str) -> bytes:
    """Run `soffice --headless --convert-to pdf` in a temp dir, return PDF bytes.

    Raises HTTPException(502) on conversion failure so the upload returns a
    clean error and the project_files row gets marked failed by the caller.
    """
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise HTTPException(502, "libreoffice not installed on backend host")
    with tempfile.TemporaryDirectory(prefix="sleek-conv-") as tmp:
        in_path = Path(tmp) / f"input.{ext}"
        in_path.write_bytes(data)
        proc = await asyncio.create_subprocess_exec(
            soffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            tmp,
            str(in_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(504, "office→pdf conversion timed out")
        if proc.returncode != 0:
            msg = (stderr or stdout or b"").decode("utf-8", "replace")[:300]
            raise HTTPException(502, f"office→pdf conversion failed: {msg}")
        out_path = Path(tmp) / "input.pdf"
        if not out_path.exists():
            raise HTTPException(502, "office→pdf conversion produced no output")
        return out_path.read_bytes()


class FileOut(BaseModel):
    id: str
    filename: str
    size_bytes: int | None = None
    status: str
    chunk_count: int | None = None
    page_count: int | None = None


class FigureRef(BaseModel):
    chunk_id: str
    figure_label: str | None = None
    page_start: int
    caption: str | None = None
    storage_path: str | None = None


class FileDetail(BaseModel):
    id: str
    filename: str
    size_bytes: int | None = None
    mime_type: str | None = None
    page_count: int | None = None
    chunk_count: int | None = None
    status: str
    ingest_error: str | None = None
    created_at: str | None = None
    block_breakdown: dict[str, int]
    outline: list[str]
    figures: list[FigureRef]


def _load_project(project_id: str, user_id: str) -> dict:
    res = (
        supabase()
        .table("projects")
        .select("id,name")
        .eq("id", project_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "project not found")
    return res.data[0]


@router.get("", response_model=list[FileOut])
def list_files(project_id: str, user_id: str = Depends(current_user_id)):
    _load_project(project_id, user_id)
    res = (
        supabase()
        .table("project_files")
        .select(
            "id,filename,size_bytes,status,chunk_count,page_count"
        )
        .eq("project_id", project_id)
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return res.data or []


@router.post("", response_model=FileOut)
@traceable(run_type="chain", name="files.upload")
async def upload_file(
    project_id: str,
    file: UploadFile = File(...),
    user_id: str = Depends(current_user_id),
):
    """Upload a PDF (or office doc) into the project's Vertex RAG corpus.

    The PDF lands in GCS at the canonical layout and the row is inserted
    with status='queued'. The LRO poller batches all queued rows for a
    project into a single rag.import_files_async call (Vertex serialises
    operations per corpus, so single-file imports collide with each other
    when uploads arrive concurrently).
    """
    _load_project(project_id, user_id)
    contents = await file.read()
    src_ext = (file.filename.rsplit(".", 1)[-1] if "." in (file.filename or "") else "").lower()

    if src_ext in _OFFICE_EXTS:
        contents = await _convert_office_to_pdf(contents, src_ext)
    elif src_ext != "pdf":
        raise HTTPException(415, f"unsupported file type: .{src_ext}")
    mime = "application/pdf"
    size_bytes = len(contents)

    # PyMuPDF page-count read; it's the only signal we still own under the
    # Vertex pipeline (RAG Engine doesn't expose page count anywhere).
    page_count: int | None = None
    try:
        import fitz
        with fitz.open(stream=contents, filetype="pdf") as doc:
            page_count = doc.page_count
    except Exception:
        page_count = None

    file_id = str(uuid.uuid4())
    try:
        gs_uri = await asyncio.to_thread(
            upload_pdf_bytes, user_id, project_id, file_id, contents, file.filename
        )
    except Exception as exc:
        raise HTTPException(502, f"GCS upload failed: {exc}") from exc

    # Make sure the corpus exists so the dispatcher can reference it. Race-safe.
    try:
        await asyncio.to_thread(ensure_corpus_for_project, project_id)
    except Exception as exc:
        raise HTTPException(502, f"corpus init failed: {exc}") from exc

    insert = (
        supabase()
        .table("project_files")
        .insert(
            {
                "id": file_id,
                "project_id": project_id,
                "user_id": user_id,
                "filename": file.filename,
                "size_bytes": size_bytes,
                "mime_type": mime,
                "gcs_blob_path": gs_uri,
                "page_count": page_count,
                "status": "queued",
            }
        )
        .execute()
    )
    row = insert.data[0]

    return FileOut(
        id=row["id"],
        filename=file.filename,
        size_bytes=size_bytes,
        status="queued",
        chunk_count=0,
        page_count=page_count,
    )


@router.get("/{file_id}", response_model=FileDetail)
def get_file_detail(
    project_id: str, file_id: str, user_id: str = Depends(current_user_id)
):
    """Rich detail for a single file: ingestion status, structure breakdown,
    section outline, and figure thumbnails. Powers the file panel's analysis
    pane after Document AI finishes."""
    _load_project(project_id, user_id)
    f_res = (
        supabase()
        .table("project_files")
        .select(
            "id,filename,size_bytes,mime_type,page_count,chunk_count,status,"
            "ingest_error,created_at"
        )
        .eq("id", file_id)
        .eq("project_id", project_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not f_res.data:
        raise HTTPException(404, "file not found")
    f = f_res.data[0]

    chunks: list[dict] = []
    page_size = 1000
    offset = 0
    while True:
        page = (
            supabase()
            .table("document_chunks")
            .select("id,chunk_index,block_type,heading_path,page_start,figure_label")
            .eq("file_id", file_id)
            .eq("user_id", user_id)
            .order("chunk_index")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = page.data or []
        chunks.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size

    breakdown: dict[str, int] = {}
    outline: list[str] = []
    seen_headings: set[str] = set()
    figure_chunks: list[dict] = []
    for c in chunks:
        bt = c.get("block_type") or "paragraph"
        breakdown[bt] = breakdown.get(bt, 0) + 1
        hp = c.get("heading_path") or []
        for h in hp:
            if h and h not in seen_headings:
                seen_headings.add(h)
                outline.append(h)
        if bt == "figure":
            figure_chunks.append(c)

    figures: list[FigureRef] = []
    if figure_chunks:
        chunk_ids = [c["id"] for c in figure_chunks]
        img_res = (
            supabase()
            .table("chunk_images")
            .select("chunk_id,storage_path")
            .in_("chunk_id", chunk_ids)
            .execute()
        )
        img_by_chunk = {r["chunk_id"]: r for r in (img_res.data or [])}
        for c in figure_chunks:
            img = img_by_chunk.get(c["id"]) or {}
            figures.append(
                FigureRef(
                    chunk_id=c["id"],
                    figure_label=c.get("figure_label"),
                    page_start=c.get("page_start") or 1,
                    caption=None,
                    storage_path=img.get("storage_path"),
                )
            )

    return FileDetail(
        id=f["id"],
        filename=f["filename"],
        size_bytes=f.get("size_bytes"),
        mime_type=f.get("mime_type"),
        page_count=f.get("page_count"),
        chunk_count=f.get("chunk_count"),
        status=f["status"],
        ingest_error=f.get("ingest_error"),
        created_at=f.get("created_at"),
        block_breakdown=breakdown,
        outline=outline,
        figures=figures,
    )


class SignedUrlOut(BaseModel):
    url: str
    expires_in: int


@router.get("/{file_id}/signed-url", response_model=SignedUrlOut)
def get_signed_url(
    project_id: str, file_id: str, user_id: str = Depends(current_user_id)
):
    """Return a short-lived signed URL for the original PDF in GCS so the
    frontend's PDF viewer can iframe it. Auth: caller must own the project
    that owns the file. PDFs live in GCS at the canonical layout
    `gs://{bucket}/{user_id}/{project_id}/{file_id}/{sanitized}.pdf`
    (see `app/gcs.py`); we resolve the row by (project, user, file) and
    sign V4 against the recorded `gcs_blob_path`.
    """
    _load_project(project_id, user_id)
    row_res = (
        supabase()
        .table("project_files")
        .select("gcs_blob_path")
        .eq("id", file_id)
        .eq("project_id", project_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not row_res.data:
        raise HTTPException(404, "file not found")
    blob_path = (row_res.data[0] or {}).get("gcs_blob_path") or ""
    if not blob_path.startswith("gs://"):
        raise HTTPException(404, "file has no GCS object")
    try:
        bucket_name, key = blob_path[len("gs://"):].split("/", 1)
    except ValueError as exc:
        raise HTTPException(500, f"invalid gcs_blob_path: {blob_path!r}") from exc
    expires_in = 600
    try:
        signed = storage_client().bucket(bucket_name).blob(key).generate_signed_url(
            version="v4",
            expiration=_dt.timedelta(seconds=expires_in),
            method="GET",
            response_disposition="inline",
            response_type="application/pdf",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"signing failed: {exc}") from exc
    return SignedUrlOut(url=signed, expires_in=expires_in)


@router.delete("/{file_id}")
def delete_file(
    project_id: str, file_id: str, user_id: str = Depends(current_user_id)
):
    _load_project(project_id, user_id)
    row_res = (
        supabase()
        .table("project_files")
        .select("gcs_blob_path")
        .eq("id", file_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not row_res.data:
        raise HTTPException(404, "file not found")
    row = row_res.data[0]
    blob_path = row.get("gcs_blob_path")

    if blob_path:
        if blob_path.startswith("gs://"):
            from app.gcs import storage_client
            try:
                bucket_name, key = blob_path[len("gs://"):].split("/", 1)
                storage_client().bucket(bucket_name).blob(key).delete()
            except Exception:
                pass
        else:
            try:
                supabase().storage.from_("project-files").remove([blob_path])
            except Exception:
                pass

    # Chunk images live in a separate bucket and aren't covered by the
    # project_files cascade. Collect their paths before the DB delete drops
    # the chunk_images rows via document_chunks → chunk_images cascade.
    chunk_imgs = (
        supabase()
        .table("chunk_images")
        .select("storage_path,document_chunks!inner(file_id)")
        .eq("user_id", user_id)
        .eq("document_chunks.file_id", file_id)
        .execute()
    )
    img_paths = [r["storage_path"] for r in (chunk_imgs.data or []) if r.get("storage_path")]
    if img_paths:
        try:
            supabase().storage.from_("chunk-images").remove(img_paths)
        except Exception:
            pass

    supabase().table("project_files").delete().eq("id", file_id).eq(
        "user_id", user_id
    ).execute()
    return {"deleted": file_id}
