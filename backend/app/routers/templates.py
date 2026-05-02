"""Endpoints fuer die per-User Projektanalyse-Vorlage (analysis_templates).

GET  /api/templates/projektanalyse  -> {questions: [...]}
PUT  /api/templates/projektanalyse  body {questions: [...]} -> {questions: [...]}

Der Backfill in Migration 0021 sorgt dafuer, dass jeder User eine Zeile hat;
fuer Robustheit faellt der GET dennoch auf default_analysis_questions zurueck,
falls die Zeile fehlt.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import current_user_id
from app.db import supabase

router = APIRouter(prefix="/api/templates", tags=["templates"])


class TemplateOut(BaseModel):
    questions: list[str]


class TemplateIn(BaseModel):
    questions: list[str] = Field(..., min_length=1, max_length=50)


def _default_questions() -> list[str]:
    res = supabase().rpc("default_analysis_questions").execute()
    return list(res.data or [])


@router.get("/projektanalyse", response_model=TemplateOut)
def get_projektanalyse_template(user_id: str = Depends(current_user_id)):
    res = (
        supabase()
        .table("analysis_templates")
        .select("questions")
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    row = res.data if res else None
    if row and row.get("questions"):
        return TemplateOut(questions=list(row["questions"]))
    return TemplateOut(questions=_default_questions())


@router.put("/projektanalyse", response_model=TemplateOut)
def put_projektanalyse_template(
    body: TemplateIn, user_id: str = Depends(current_user_id)
):
    cleaned = [q.strip() for q in body.questions if q and q.strip()]
    if not cleaned:
        raise HTTPException(
            status_code=400, detail="questions darf nicht leer sein"
        )
    (
        supabase()
        .table("analysis_templates")
        .upsert(
            {"user_id": user_id, "questions": cleaned, "updated_at": "now()"},
            on_conflict="user_id",
        )
        .execute()
    )
    return TemplateOut(questions=cleaned)
