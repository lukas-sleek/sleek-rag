import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from langsmith import traceable
from pydantic import BaseModel

from app.auth import current_user_id
from app.db import supabase
from app.openai_client import vs_create, vs_delete

logger = logging.getLogger(__name__)


def _provision_vector_store(project_id: str, name: str) -> None:
    """Background: create the OpenAI vector store and attach it to the project
    row. Conditional on the column still being null so we don't clobber a
    vs_id that the lazy upload path may have written in the meantime; if we
    lose that race, delete the orphan we just created."""
    try:
        vs_id = vs_create(name=name)
    except Exception:
        logger.exception("vector store create failed for project %s", project_id)
        return
    try:
        res = (
            supabase()
            .table("projects")
            .update({"openai_vector_store_id": vs_id})
            .eq("id", project_id)
            .is_("openai_vector_store_id", None)
            .execute()
        )
    except Exception:
        logger.exception("vector store row update failed for project %s", project_id)
        try:
            vs_delete(vs_id)
        except Exception:
            pass
        return
    if not res.data:
        try:
            vs_delete(vs_id)
        except Exception:
            pass

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectIn(BaseModel):
    name: str


class ProjectPatch(BaseModel):
    name: str


class ChatStub(BaseModel):
    id: str
    title: str


class ProjectOut(BaseModel):
    id: str
    name: str
    has_files: bool = False
    chats: list[ChatStub] = []


@router.get("", response_model=list[ProjectOut])
@traceable(run_type="chain", name="projects.list")
def list_projects(user_id: str = Depends(current_user_id)):
    proj_res = (
        supabase()
        .table("projects")
        .select("id,name")
        .eq("user_id", user_id)
        .order("created_at")
        .execute()
    )
    projects = proj_res.data or []
    if not projects:
        return []
    chats_res = (
        supabase()
        .table("chats")
        .select("id,project_id,title")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    by_project: dict[str, list[ChatStub]] = {p["id"]: [] for p in projects}
    for c in chats_res.data or []:
        bucket = by_project.setdefault(c["project_id"], [])
        bucket.append(ChatStub(id=c["id"], title=c["title"]))
    files_res = (
        supabase()
        .table("project_files")
        .select("project_id")
        .eq("user_id", user_id)
        .execute()
    )
    has_files: set[str] = {row["project_id"] for row in files_res.data or []}
    return [
        ProjectOut(
            id=p["id"],
            name=p["name"],
            has_files=p["id"] in has_files,
            chats=by_project.get(p["id"], []),
        )
        for p in projects
    ]


@router.post("", response_model=ProjectOut)
@traceable(run_type="chain", name="projects.create")
def create_project(
    body: ProjectIn,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(current_user_id),
):
    res = (
        supabase()
        .table("projects")
        .insert({"user_id": user_id, "name": body.name})
        .execute()
    )
    row = res.data[0]
    background_tasks.add_task(_provision_vector_store, row["id"], body.name)
    return ProjectOut(id=row["id"], name=row["name"], has_files=False, chats=[])


@router.patch("/{project_id}", response_model=ProjectOut)
@traceable(run_type="chain", name="projects.rename")
def rename_project(
    project_id: str, body: ProjectPatch, user_id: str = Depends(current_user_id)
):
    res = (
        supabase()
        .table("projects")
        .update({"name": body.name})
        .eq("id", project_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "not found")
    row = res.data[0]
    return ProjectOut(id=row["id"], name=row["name"], has_files=False, chats=[])


@router.delete("/{project_id}")
@traceable(run_type="chain", name="projects.delete")
def delete_project(project_id: str, user_id: str = Depends(current_user_id)):
    existing = (
        supabase()
        .table("projects")
        .select("id,openai_vector_store_id")
        .eq("id", project_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not existing.data:
        raise HTTPException(404, "not found")
    vs_id = existing.data[0].get("openai_vector_store_id")
    res = (
        supabase()
        .table("projects")
        .delete()
        .eq("id", project_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "not found")
    if vs_id:
        try:
            vs_delete(vs_id)
        except Exception:
            pass
    return {"deleted": project_id}
