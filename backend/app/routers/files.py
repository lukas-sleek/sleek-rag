from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from langsmith import traceable
from pydantic import BaseModel

from app.auth import current_user_id
from app.db import supabase
from app.openai_client import (
    files_create,
    files_delete,
    vs_create,
    vs_delete_file,
    vs_ingest_file,
)

router = APIRouter(prefix="/api/projects/{project_id}/files", tags=["files"])


class FileOut(BaseModel):
    id: str
    filename: str
    size_bytes: int | None = None
    status: str
    openai_file_id: str | None = None


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


def _ensure_vector_store(project: dict) -> str:
    """Lazy-create a per-project OpenAI vector store on first upload (legacy
    path; new projects already have a vs_id provisioned at creation time)."""
    if project.get("openai_vector_store_id"):
        return project["openai_vector_store_id"]
    vs_id = vs_create(name=project["name"])
    supabase().table("projects").update(
        {"openai_vector_store_id": vs_id}
    ).eq("id", project["id"]).execute()
    project["openai_vector_store_id"] = vs_id
    return vs_id


@router.get("", response_model=list[FileOut])
@traceable(run_type="chain", name="files.list")
def list_files(project_id: str, user_id: str = Depends(current_user_id)):
    _load_project(project_id, user_id)
    res = (
        supabase()
        .table("project_files")
        .select("id,filename,size_bytes,status,openai_file_id")
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
    project = _load_project(project_id, user_id)

    contents = await file.read()
    size_bytes = len(contents)

    try:
        vector_store_id = _ensure_vector_store(project)
    except Exception as exc:
        raise HTTPException(502, f"vector store create failed: {exc}") from exc

    try:
        openai_file = files_create(filename=file.filename, contents=contents)
    except Exception as exc:
        raise HTTPException(502, f"openai file upload failed: {exc}") from exc

    try:
        ingest = vs_ingest_file(
            vector_store_id=vector_store_id, file_id=openai_file["id"]
        )
    except Exception as exc:
        raise HTTPException(502, f"vector store ingest failed: {exc}") from exc

    status = (
        "indexed" if ingest["status"] == "completed" else (ingest["status"] or "failed")
    )

    insert = (
        supabase()
        .table("project_files")
        .insert(
            {
                "project_id": project_id,
                "user_id": user_id,
                "filename": file.filename,
                "size_bytes": size_bytes,
                "openai_file_id": openai_file["id"],
                "status": status,
            }
        )
        .execute()
    )
    return insert.data[0]


@router.delete("/{file_id}")
@traceable(run_type="chain", name="files.delete")
def delete_file(
    project_id: str, file_id: str, user_id: str = Depends(current_user_id)
):
    project = _load_project(project_id, user_id)
    row_res = (
        supabase()
        .table("project_files")
        .select("openai_file_id")
        .eq("id", file_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not row_res.data:
        raise HTTPException(404, "file not found")
    openai_file_id = row_res.data[0]["openai_file_id"]
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

    supabase().table("project_files").delete().eq("id", file_id).eq(
        "user_id", user_id
    ).execute()
    return {"deleted": file_id}
