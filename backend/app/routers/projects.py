from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import current_user_id
from app.db import supabase

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
def create_project(body: ProjectIn, user_id: str = Depends(current_user_id)):
    res = (
        supabase()
        .table("projects")
        .insert({"user_id": user_id, "name": body.name})
        .execute()
    )
    row = res.data[0]
    return ProjectOut(id=row["id"], name=row["name"], has_files=False, chats=[])


@router.patch("/{project_id}", response_model=ProjectOut)
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
def delete_project(project_id: str, user_id: str = Depends(current_user_id)):
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
    return {"deleted": project_id}
