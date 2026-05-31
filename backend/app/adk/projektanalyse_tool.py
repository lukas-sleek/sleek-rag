"""run_projektanalyse — Orchestrator-Tool fuer die Vorlagen des Nutzers.

Lebenszyklus:
  1. Orchestrator erkennt Vorlagen-Wunsch (siehe Instructions).
  2. Orchestrator ruft run_projektanalyse() auf — optional mit template_name,
     wenn der Nutzer eine bestimmte Vorlage benennt.
  3. Tool liest die in Supabase (user_templates) hinterlegten Vorlagen fuer den
     aktuellen User, waehlt die passende aus und faechert ihre Fragen ueber
     rag_specialist auf — identisch zu dispatch_rag_questions, nur dass die
     Fragen aus der User-Vorlage kommen statt aus dem Tool-Argument.
  4. Rueckgabe: {"answers": [{"question", "answer"}, ...], "template_name": str}
     — selbe Form wie dispatch_rag_questions, also greift die bestehende
     ANTWORT-AGGREGATION-Regel im Orchestrator unveraendert.

Aufloesung der Vorlage (template_name):
  - template_name gegeben -> case-insensitive exakter Match auf name, sonst
    Substring-Match. Kein Treffer -> notice mit verfuegbaren Namen.
  - template_name leer/None -> genau eine Vorlage: diese; mehrere: bevorzugt
    'Projektanalyse' (Rueckwaerts-Kompatibilitaet), sonst notice mit Auswahl;
    keine: notice "keine Vorlage hinterlegt".
"""
from __future__ import annotations

import logging

from google.adk.tools import FunctionTool, ToolContext
from google.adk.agents.llm_agent import LlmAgent

from app.db import supabase

from .dispatch_rag_questions_tool import fanout_rag_specialist
from .streaming_agent_tool import StreamingAgentTool

log = logging.getLogger(__name__)


def _load_templates(user_id: str) -> list[dict]:
    """Liest alle Vorlagen des Nutzers aus public.user_templates."""
    try:
        res = (
            supabase()
            .table("user_templates")
            .select("id, name, questions, position")
            .eq("user_id", user_id)
            .order("position")
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("user_templates lookup failed for %s: %s", user_id, exc)
        return []
    out: list[dict] = []
    for row in res.data or []:
        questions = [
            q.strip()
            for q in (row.get("questions") or [])
            if isinstance(q, str) and q.strip()
        ]
        out.append({"name": row.get("name") or "", "questions": questions})
    return out


def _available_names(templates: list[dict]) -> str:
    return ", ".join(f"«{t['name']}»" for t in templates) or "(keine)"


def _resolve_template(
    templates: list[dict], template_name: str | None
) -> tuple[dict | None, str | None]:
    """Waehlt die passende Vorlage. Rueckgabe: (template, notice).

    Genau einer von beiden ist gesetzt: bei Erfolg template, sonst notice.
    """
    if not templates:
        return None, (
            "Keine Vorlage hinterlegt. Der Nutzer kann Vorlagen ueber den "
            "Button 'Vorlagen' konfigurieren."
        )

    name = (template_name or "").strip()
    if name:
        low = name.lower()
        exact = [t for t in templates if t["name"].strip().lower() == low]
        if exact:
            return exact[0], None
        substr = [t for t in templates if low in t["name"].strip().lower()]
        if len(substr) == 1:
            return substr[0], None
        if len(substr) > 1:
            return None, (
                f"Mehrere Vorlagen passen auf «{name}»: "
                f"{_available_names(substr)}. Welche soll ich verwenden?"
            )
        return None, (
            f"Keine Vorlage namens «{name}». Verfuegbar: "
            f"{_available_names(templates)}."
        )

    # Kein Name angegeben.
    if len(templates) == 1:
        return templates[0], None
    pa = [t for t in templates if t["name"].strip().lower() == "projektanalyse"]
    if pa:
        return pa[0], None
    return None, (
        f"Mehrere Vorlagen vorhanden: {_available_names(templates)}. "
        "Welche soll ich durchgehen?"
    )


def make_run_projektanalyse_tool(rag_specialist: LlmAgent) -> FunctionTool:
    """Faktorisiert das FunctionTool, das ueber rag_specialist closuret —
    der Orchestrator sieht es als run_projektanalyse(template_name?)."""

    async def run_projektanalyse(
        tool_context: ToolContext, template_name: str | None = None
    ) -> dict:
        """Beantwortet eine Vorlage des Nutzers.

        Verwende dieses Tool, wenn der Nutzer eine Projektanalyse bzw. eine
        seiner Vorlagen durchgehen moechte (Formulierungen wie "Projektanalyse
        erstellen", "mach eine Projektanalyse", "geh die Vorlage durch",
        "erstelle die Analyse anhand der Vorlage X").

        Args:
            template_name: Optionaler Name der gewuenschten Vorlage. Uebergib
                den Namen, wenn der Nutzer eine bestimmte Vorlage benennt
                (z.B. 'Termine' bei 'geh die Vorlage Termine durch' oder bei
                'Erstelle die Projektanalyse anhand der Vorlage "Termine"').
                Lass das Argument weg, wenn der Nutzer generisch eine
                Projektanalyse anfordert ohne eine Vorlage zu benennen.

        Das Tool laedt die in den Nutzer-Einstellungen hinterlegten Vorlagen
        aus Supabase, waehlt die passende aus und beantwortet ihre Fragen
        parallel ueber den rag_specialist (gleicher Mechanismus wie
        dispatch_rag_questions).

        Returns:
            {"answers": [{"question": str, "answer": str}, ...],
             "template_name": str} in der vom Nutzer konfigurierten Reihenfolge,
            oder {"answers": [], "notice": str} wenn keine eindeutige Vorlage
            ermittelt werden konnte.
        """
        user_id = tool_context._invocation_context.user_id
        templates = await _load_templates_async(user_id)
        template, notice = _resolve_template(templates, template_name)
        if template is None:
            return {"answers": [], "notice": notice}

        questions = template["questions"]
        if not questions:
            return {
                "answers": [],
                "notice": (
                    f"Die Vorlage «{template['name']}» hat keine Fragen. Der "
                    "Nutzer kann sie ueber den Button 'Vorlagen' befuellen."
                ),
            }

        results = await fanout_rag_specialist(
            rag_specialist, questions, user_id=user_id
        )

        existing_chunks = list(
            tool_context.state.get("agent_grounding_chunks", []) or []
        )
        answers: list[dict] = []
        for idx, (q, (text, gm)) in enumerate(zip(questions, results)):
            offset = len(existing_chunks)
            if gm is not None:
                text = StreamingAgentTool._annotate_with_grounding_supports(
                    text, gm, idx_offset=offset
                )
                chunk_confidence = StreamingAgentTool._per_chunk_confidence(gm)
                for ci, c in enumerate(gm.grounding_chunks or []):
                    rc = getattr(c, "retrieved_context", None)
                    if rc is None:
                        continue
                    entry = {
                        "agent": "rag_specialist",
                        "text": getattr(rc, "text", "") or "",
                        "title": getattr(rc, "title", "") or "",
                        "uri": getattr(rc, "uri", "") or "",
                        "confidence": chunk_confidence.get(ci),
                    }
                    rag_chunk = getattr(rc, "rag_chunk", None)
                    if rag_chunk is not None:
                        entry["rag_chunk_text"] = getattr(rag_chunk, "text", "") or ""
                        page_span = getattr(rag_chunk, "page_span", None)
                        if page_span is not None:
                            entry["page_first"] = getattr(page_span, "first_page", None)
                            entry["page_last"] = getattr(page_span, "last_page", None)
                    existing_chunks.append(entry)
                # Activity-panel row per template question.
                StreamingAgentTool._append_retrieval_trace(
                    tool_context, gm, label_suffix=f"-pa{idx}",
                )
            answers.append({"question": q, "answer": text})

        tool_context.state["agent_grounding_chunks"] = existing_chunks
        return {"answers": answers, "template_name": template["name"]}

    return FunctionTool(func=run_projektanalyse)


async def _load_templates_async(user_id: str) -> list[dict]:
    """Sync supabase-py call ueber to_thread, damit die Tool-Invocation
    nicht das Event-Loop blockiert."""
    import asyncio
    return await asyncio.to_thread(_load_templates, user_id)
