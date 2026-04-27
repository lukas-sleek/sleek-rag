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
    name: str | None = None
    expanded: bool | None = None


class ChatStub(BaseModel):
    id: str
    title: str


class ProjectOut(BaseModel):
    id: str
    name: str
    has_files: bool = False
    expanded: bool = False
    chats: list[ChatStub] = []


@router.get("", response_model=list[ProjectOut])
@traceable(run_type="chain", name="projects.list")
def list_projects(user_id: str = Depends(current_user_id)):
    # sort_order asc (nulls last) is the persisted drag-drop order; new
    # projects get min(sort_order)-1 in create_project so they land on top
    # without an explicit reorder. Pre-existing rows with null sort_order
    # tiebreak by created_at desc so newest-first still holds.
    proj_res = (
        supabase()
        .table("projects")
        .select("id,name,expanded")
        .eq("user_id", user_id)
        .order("sort_order", desc=False, nullsfirst=False)
        .order("created_at", desc=True)
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
            expanded=bool(p.get("expanded")),
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
    # New projects land at min(sort_order)-1 so they appear at the top of
    # the sidebar across reloads. If no rows have an explicit sort_order
    # yet (fresh user, or pre-migration), we leave it null and let the
    # created_at desc fallback in list_projects do the work.
    cur_min = (
        supabase()
        .table("projects")
        .select("sort_order")
        .eq("user_id", user_id)
        .not_.is_("sort_order", "null")
        .order("sort_order", desc=False, nullsfirst=False)
        .limit(1)
        .execute()
    )
    next_sort: int | None = None
    if cur_min.data and cur_min.data[0].get("sort_order") is not None:
        next_sort = cur_min.data[0]["sort_order"] - 1

    # User-created projects open expanded — they just made it, they want
    # to see the chats inside. The signup-trigger Playground stays
    # collapsed and gets auto-expanded by the frontend's "active project
    # is expanded on initial load" rule.
    payload: dict = {"user_id": user_id, "name": body.name, "expanded": True}
    if next_sort is not None:
        payload["sort_order"] = next_sort

    res = supabase().table("projects").insert(payload).execute()
    row = res.data[0]
    background_tasks.add_task(_provision_vector_store, row["id"], body.name)
    return ProjectOut(
        id=row["id"],
        name=row["name"],
        has_files=False,
        expanded=bool(row.get("expanded", True)),
        chats=[],
    )


class ProjectOrderIn(BaseModel):
    project_ids: list[str]


@router.put("/order")
@traceable(run_type="chain", name="projects.reorder")
def reorder_projects(
    body: ProjectOrderIn, user_id: str = Depends(current_user_id)
):
    if not body.project_ids:
        return {"updated": 0}
    # Confirm every id belongs to this user before writing — otherwise a
    # caller could spread sort_order writes across other users' projects.
    owned = (
        supabase()
        .table("projects")
        .select("id")
        .eq("user_id", user_id)
        .in_("id", body.project_ids)
        .execute()
    )
    owned_ids = {row["id"] for row in (owned.data or [])}
    missing = [pid for pid in body.project_ids if pid not in owned_ids]
    if missing:
        raise HTTPException(404, f"unknown project ids: {missing}")
    for idx, pid in enumerate(body.project_ids):
        supabase().table("projects").update({"sort_order": idx}).eq(
            "id", pid
        ).eq("user_id", user_id).execute()
    return {"updated": len(body.project_ids)}


@router.patch("/{project_id}", response_model=ProjectOut)
@traceable(run_type="chain", name="projects.update")
def update_project(
    project_id: str, body: ProjectPatch, user_id: str = Depends(current_user_id)
):
    update: dict = {}
    if body.name is not None:
        update["name"] = body.name
    if body.expanded is not None:
        update["expanded"] = body.expanded
    if not update:
        raise HTTPException(400, "no fields to update")
    res = (
        supabase()
        .table("projects")
        .update(update)
        .eq("id", project_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "not found")
    row = res.data[0]
    return ProjectOut(
        id=row["id"],
        name=row["name"],
        has_files=False,
        expanded=bool(row.get("expanded")),
        chats=[],
    )


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
