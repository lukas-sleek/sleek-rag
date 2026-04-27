from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from langsmith import traceable
from pydantic import BaseModel

from app.auth import current_user_id
from app.db import supabase
from app.openai_client import files_delete, vs_delete_file

router = APIRouter(prefix="/api/projects/{project_id}/files", tags=["files"])


class FileOut(BaseModel):
    id: str
    filename: str
    size_bytes: int | None = None
    status: str
    chunk_count: int | None = None
    page_count: int | None = None
    openai_file_id: str | None = None  # legacy, dropped in plan 13


@traceable(run_type="tool", name="db.load_project")
def _load_project(project_id: str, user_id: str) -> dict:
    res = (
        supabase()
        .table("projects")
        .select("id,name,openai_vector_store_id")
        .eq("id", project_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "project not found")
    return res.data[0]


@router.get("", response_model=list[FileOut])
@traceable(run_type="chain", name="files.list")
def list_files(project_id: str, user_id: str = Depends(current_user_id)):
    _load_project(project_id, user_id)
    res = (
        supabase()
        .table("project_files")
        .select(
            "id,filename,size_bytes,status,chunk_count,page_count,openai_file_id"
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
    _load_project(project_id, user_id)
    contents = await file.read()
    size_bytes = len(contents)
    mime = file.content_type or "application/octet-stream"

    insert = (
        supabase()
        .table("project_files")
        .insert(
            {
                "project_id": project_id,
                "user_id": user_id,
                "filename": file.filename,
                "size_bytes": size_bytes,
                "mime_type": mime,
                "status": "uploading",
            }
        )
        .execute()
    )
    file_row = insert.data[0]
    file_id = file_row["id"]

    blob_path = f"{user_id}/{file_id}/{file.filename}"
    try:
        supabase().storage.from_("project-files").upload(
            blob_path, contents, {"content-type": mime}
        )
    except Exception as exc:
        supabase().table("project_files").update(
            {"status": "failed", "ingest_error": f"storage upload failed: {exc}"[:500]}
        ).eq("id", file_id).execute()
        raise HTTPException(502, f"storage upload failed: {exc}") from exc

    supabase().table("project_files").update(
        {"gcs_blob_path": blob_path, "status": "parsing"}
    ).eq("id", file_id).execute()

    supabase().table("ingest_jobs").insert(
        {"file_id": file_id, "user_id": user_id, "state": "queued"}
    ).execute()

    return FileOut(
        id=file_id,
        filename=file.filename,
        size_bytes=size_bytes,
        status="parsing",
        chunk_count=0,
        page_count=None,
        openai_file_id=None,
    )


@router.delete("/{file_id}")
@traceable(run_type="chain", name="files.delete")
def delete_file(
    project_id: str, file_id: str, user_id: str = Depends(current_user_id)
):
    project = _load_project(project_id, user_id)
    row_res = (
        supabase()
        .table("project_files")
        .select("openai_file_id,gcs_blob_path")
        .eq("id", file_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not row_res.data:
        raise HTTPException(404, "file not found")
    row = row_res.data[0]
    openai_file_id = row.get("openai_file_id")
    blob_path = row.get("gcs_blob_path")
    vector_store_id = project.get("openai_vector_store_id")

    if openai_file_id and vector_store_id:
        try:
            vs_delete_file(vector_store_id=vector_store_id, file_id=openai_file_id)
        except Exception:
            pass
        try:
            files_delete(openai_file_id)
        except Exception:
            pass

    if blob_path:
        try:
            supabase().storage.from_("project-files").remove([blob_path])
        except Exception:
            pass

    supabase().table("project_files").delete().eq("id", file_id).eq(
        "user_id", user_id
    ).execute()
    return {"deleted": file_id}
