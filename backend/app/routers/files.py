from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.auth import current_user_id
from app.db import supabase
from app.openai_client import openai_client

router = APIRouter(prefix="/api/projects/{project_id}/files", tags=["files"])


class FileOut(BaseModel):
    id: str
    filename: str
    size_bytes: int | None = None
    status: str
    openai_file_id: str | None = None


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
    """Lazy-create a per-project OpenAI vector store on first upload."""
    if project.get("openai_vector_store_id"):
        return project["openai_vector_store_id"]
    vs = openai_client().vector_stores.create(name=project["name"])
    supabase().table("projects").update(
        {"openai_vector_store_id": vs.id}
    ).eq("id", project["id"]).execute()
    project["openai_vector_store_id"] = vs.id
    return vs.id


@router.get("", response_model=list[FileOut])
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
        openai_file = openai_client().files.create(
            file=(file.filename, contents),
            purpose="user_data",
        )
    except Exception as exc:
        raise HTTPException(502, f"openai file upload failed: {exc}") from exc

    try:
        poll = openai_client().vector_stores.files.create_and_poll(
            vector_store_id=vector_store_id,
            file_id=openai_file.id,
        )
    except Exception as exc:
        raise HTTPException(502, f"vector store ingest failed: {exc}") from exc

    status = "indexed" if poll.status == "completed" else (poll.status or "failed")

    insert = (
        supabase()
        .table("project_files")
        .insert(
            {
                "project_id": project_id,
                "user_id": user_id,
                "filename": file.filename,
                "size_bytes": size_bytes,
                "openai_file_id": openai_file.id,
                "status": status,
            }
        )
        .execute()
    )
    return insert.data[0]


@router.delete("/{file_id}")
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
            openai_client().vector_stores.files.delete(
                vector_store_id=vector_store_id,
                file_id=openai_file_id,
            )
        except Exception:
            pass
        try:
            openai_client().files.delete(openai_file_id)
        except Exception:
            pass

    supabase().table("project_files").delete().eq("id", file_id).eq(
        "user_id", user_id
    ).execute()
    return {"deleted": file_id}
