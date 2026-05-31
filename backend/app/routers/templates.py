"""Endpoints fuer die per-User Vorlagen (user_templates).

Jeder Nutzer kann bis zu 4 frei benannte Vorlagen anlegen. Jede Vorlage ist
eine geordnete Liste von Fragen, die der Orchestrator ueber run_projektanalyse
als Batch durch rag_specialist schickt.

GET    /api/templates            -> [{id, name, questions, position}, ...]
POST   /api/templates            body {name?, questions?} -> TemplateOut
PUT    /api/templates/{id}        body {name?, questions?} -> TemplateOut
DELETE /api/templates/{id}        -> 204

Obergrenze 4 wird sowohl hier (409) als auch per DB-Trigger erzwungen.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.auth import current_user_id
from app.db import supabase

router = APIRouter(prefix="/api/templates", tags=["templates"])

MAX_TEMPLATES = 4


class TemplateOut(BaseModel):
    id: str
    name: str
    questions: list[str]
    position: int


class TemplateCreate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    questions: list[str] = Field(default_factory=list, max_length=50)


class TemplateUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    questions: list[str] | None = Field(default=None, max_length=50)


def _clean_questions(questions: list[str]) -> list[str]:
    return [q.strip() for q in questions if q and q.strip()]


def _row_to_out(row: dict) -> TemplateOut:
    return TemplateOut(
        id=str(row["id"]),
        name=row["name"],
        questions=list(row.get("questions") or []),
        position=int(row["position"]),
    )


@router.get("", response_model=list[TemplateOut])
def list_templates(user_id: str = Depends(current_user_id)):
    res = (
        supabase()
        .table("user_templates")
        .select("id, name, questions, position")
        .eq("user_id", user_id)
        .order("position")
        .execute()
    )
    return [_row_to_out(r) for r in (res.data or [])]


@router.post("", response_model=TemplateOut, status_code=201)
def create_template(
    body: TemplateCreate, user_id: str = Depends(current_user_id)
):
    res = (
        supabase()
        .table("user_templates")
        .select("position")
        .eq("user_id", user_id)
        .execute()
    )
    used = {int(r["position"]) for r in (res.data or [])}
    if len(used) >= MAX_TEMPLATES:
        raise HTTPException(
            status_code=409,
            detail=f"Maximal {MAX_TEMPLATES} Vorlagen pro Nutzer erlaubt",
        )
    # Kleinster freier Slot in 0..3 (haelt Positionen dicht nach Loeschungen).
    position = next(p for p in range(MAX_TEMPLATES) if p not in used)
    name = (body.name or "").strip() or f"Vorlage {position + 1}"
    ins = (
        supabase()
        .table("user_templates")
        .insert(
            {
                "user_id": user_id,
                "name": name,
                "questions": _clean_questions(body.questions),
                "position": position,
            }
        )
        .execute()
    )
    return _row_to_out(ins.data[0])


@router.put("/{template_id}", response_model=TemplateOut)
def update_template(
    template_id: str,
    body: TemplateUpdate,
    user_id: str = Depends(current_user_id),
):
    patch: dict = {"updated_at": "now()"}
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="name darf nicht leer sein")
        patch["name"] = name
    if body.questions is not None:
        patch["questions"] = _clean_questions(body.questions)

    res = (
        supabase()
        .table("user_templates")
        .update(patch)
        .eq("id", template_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Vorlage nicht gefunden")
    return _row_to_out(res.data[0])


@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: str, user_id: str = Depends(current_user_id)):
    res = (
        supabase()
        .table("user_templates")
        .delete()
        .eq("id", template_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Vorlage nicht gefunden")
    return Response(status_code=204)
