"""run_projektanalyse — Orchestrator-Tool fuer die Projektanalyse-Vorlage.

Lebenszyklus:
  1. Orchestrator erkennt Projektanalyse-Wunsch (siehe Instructions).
  2. Orchestrator ruft run_projektanalyse() OHNE Argumente auf.
  3. Tool liest die in Supabase (analysis_templates) hinterlegte Fragenliste
     fuer den aktuellen User und faechert sie ueber rag_specialist auf —
     identisch zu dispatch_rag_questions, nur dass die Fragen aus der
     User-Vorlage kommen statt aus dem Tool-Argument.
  4. Rueckgabe: {"answers": [{"question", "answer"}, ...]} — selbe Form
     wie dispatch_rag_questions, also greift die bestehende
     ANTWORT-AGGREGATION-Regel im Orchestrator unveraendert.
"""
from __future__ import annotations

import logging

from google.adk.tools import FunctionTool, ToolContext
from google.adk.agents.llm_agent import LlmAgent

from app.db import supabase

from .dispatch_rag_questions_tool import fanout_rag_specialist
from .streaming_agent_tool import StreamingAgentTool

log = logging.getLogger(__name__)


def _load_template_questions(user_id: str) -> list[str]:
    """Liest die Vorlagen-Fragen aus public.analysis_templates."""
    try:
        res = (
            supabase()
            .table("analysis_templates")
            .select("questions")
            .eq("user_id", user_id)
            .single()
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("analysis_templates lookup failed for %s: %s", user_id, exc)
        return []
    raw = (res.data or {}).get("questions") or []
    return [q.strip() for q in raw if isinstance(q, str) and q.strip()]


def make_run_projektanalyse_tool(rag_specialist: LlmAgent) -> FunctionTool:
    """Faktorisiert das FunctionTool, das ueber rag_specialist closuret —
    der Orchestrator sieht es als run_projektanalyse() ohne Argumente."""

    async def run_projektanalyse(tool_context: ToolContext) -> dict:
        """Beantwortet die Projektanalyse-Vorlage des Nutzers.

        Verwende dieses Tool, wenn der Nutzer eine Projektanalyse anfordert
        (Formulierungen wie "Projektanalyse erstellen", "erstelle mir die
        Projektanalyse", "mach eine Projektanalyse", "Vorlage durchgehen").
        Das Tool laedt die in den Nutzer-Einstellungen hinterlegte
        Fragenliste aus Supabase und beantwortet alle Fragen parallel ueber
        den rag_specialist (gleicher Mechanismus wie
        dispatch_rag_questions). Es nimmt KEINE Argumente — die Fragen
        kommen aus der Datenbank.

        Returns:
            {"answers": [{"question": str, "answer": str}, ...]} —
            in der vom Nutzer konfigurierten Reihenfolge.
        """
        user_id = tool_context._invocation_context.user_id
        questions = await _load_template_questions_async(user_id)
        if not questions:
            return {
                "answers": [],
                "notice": (
                    "Keine Projektanalyse-Vorlage hinterlegt. Der Nutzer "
                    "kann Fragen ueber den Button 'Vorlage Analyse' "
                    "konfigurieren."
                ),
            }

        results = await fanout_rag_specialist(
            rag_specialist, questions, user_id=user_id
        )

        existing_chunks = list(
            tool_context.state.get("agent_grounding_chunks", []) or []
        )
        answers: list[dict] = []
        for q, (text, gm) in zip(questions, results):
            offset = len(existing_chunks)
            if gm is not None:
                text = StreamingAgentTool._annotate_with_grounding_supports(
                    text, gm, idx_offset=offset
                )
                for c in gm.grounding_chunks or []:
                    rc = getattr(c, "retrieved_context", None)
                    if rc is None:
                        continue
                    entry = {
                        "agent": "rag_specialist",
                        "text": getattr(rc, "text", "") or "",
                        "title": getattr(rc, "title", "") or "",
                        "uri": getattr(rc, "uri", "") or "",
                    }
                    rag_chunk = getattr(rc, "rag_chunk", None)
                    if rag_chunk is not None:
                        entry["rag_chunk_text"] = getattr(rag_chunk, "text", "") or ""
                        page_span = getattr(rag_chunk, "page_span", None)
                        if page_span is not None:
                            entry["page_first"] = getattr(page_span, "first_page", None)
                            entry["page_last"] = getattr(page_span, "last_page", None)
                    existing_chunks.append(entry)
            answers.append({"question": q, "answer": text})

        tool_context.state["agent_grounding_chunks"] = existing_chunks
        return {"answers": answers}

    return FunctionTool(func=run_projektanalyse)


async def _load_template_questions_async(user_id: str) -> list[str]:
    """Sync supabase-py call ueber to_thread, damit die Tool-Invocation
    nicht das Event-Loop blockiert."""
    import asyncio
    return await asyncio.to_thread(_load_template_questions, user_id)
