"""Reasoning Agent / SCA — sufficiency check (plan 17.2 T6).

Prompts Gemini to assess whether the chunks the agent has retrieved so far
are sufficient to answer the user's question. Implementation follows the
Sufficient Context Awareness pattern from Google Research and the Vertex
AI RAG Engine Reasoning Agent — a prompted autorater, no fine-tuning.

Wired into the chat agent loop as a guard between the model emitting a
"no more tool calls" iteration and us streaming the final answer:

  - sufficient=true → stream the answer.
  - sufficient=false AND iterations remaining → append the autorater's
    feedback as a system message and let the agent retrieve more.
  - sufficient=false AND at iteration cap → stream the answer with a
    soft note that coverage may be incomplete.

Cost: one Gemini call per chat turn that retrieved ≥1 chunk (~500ms,
~2k tokens). Skipped on smalltalk turns.
"""
from __future__ import annotations

import json
import logging
from typing import TypedDict

from langsmith import traceable

from app.config import settings
from app.gemini_client import gemini_client
from app.retrieval import RetrievedChunk

log = logging.getLogger(__name__)


class SufficiencyVerdict(TypedDict):
    sufficient: bool
    missing: str | None
    feedback: str | None


_INSTRUCTION = (
    "Du bist ein Sufficient-Context-Rater für ein RAG-System. Du "
    "bekommst (1) eine Nutzerfrage und (2) eine Liste von Chunk-"
    "Excerpts, die das System aus den Projektdokumenten retrieved hat. "
    "Deine Aufgabe: entscheide, ob die Chunks ausreichen, um die "
    "Nutzerfrage VOLLSTÄNDIG und ohne Lücken zu beantworten.\n\n"
    "RUBRIK:\n"
    "• `sufficient=true`, wenn jeder kerntragende Aspekt der Frage "
    "in den Chunks belegt ist. Bei Aggregationsfragen ('welche', "
    "'alle', 'liste auf'): sind ALLE Entitäten enthalten, oder ist "
    "explizit aus den Chunks ableitbar, dass es nur diese gibt?\n"
    "• `sufficient=false`, wenn ein klar erwartbares Element fehlt. "
    "Beispiele: nur ein Bauherr genannt aber Frage ist 'welche "
    "Bauherren', kein Total bei Frage nach Bausumme, keine Termine "
    "der zweiten Hälfte des Projektzeitraums genannt.\n\n"
    "Bei `sufficient=false` MUSST du auch konkret in `missing` "
    "beschreiben WAS fehlt (1 Satz), und in `feedback` einen "
    "konkreten Vorschlag geben, welche Tools/Section/Datei das "
    "System als nächstes abfragen sollte.\n\n"
    "Bias: lieber `sufficient=true` als ein false-negative. Nur "
    "wenn ein offensichtlicher Faktenmangel besteht, gib false zurück.\n\n"
    "ANTWORTE AUSSCHLIESSLICH MIT JSON IN DIESEM FORMAT:\n"
    '{"sufficient": true|false, "missing": "..." oder null, '
    '"feedback": "..." oder null}'
)


_RATER_EXCERPT_CHARS = 600  # plan 17.3 T5: bumped from 200 — fact-bearing
# chunks (figure captions naming a Projektleiter, table rows with totals) were
# being truncated before the entity appeared. 600 chars × 30 chunks ≈ 18k
# chars / ~6k tokens — still well under Gemini Flash's context budget.
_RATER_MAX_CHUNKS = 30


def _format_chunks_for_rater(chunks: list[RetrievedChunk]) -> str:
    """Render chunks compactly: filename + page + first ~600 chars.
    Caps total chunk count to keep the rater prompt bounded on big turns."""
    if not chunks:
        return "(Keine Chunks retrieved.)"
    lines: list[str] = []
    for i, c in enumerate(chunks[:_RATER_MAX_CHUNKS], 1):
        excerpt = c.content[:_RATER_EXCERPT_CHARS]
        if len(c.content) > _RATER_EXCERPT_CHARS:
            excerpt += "…"
        head = f"[{i}] {c.filename} S.{c.page_start}"
        if c.figure_label:
            head += f" — {c.figure_label}"
        lines.append(f"{head}\n{excerpt}")
    if len(chunks) > _RATER_MAX_CHUNKS:
        lines.append(
            f"…(+{len(chunks) - _RATER_MAX_CHUNKS} weitere Chunks ausgelassen)"
        )
    return "\n\n".join(lines)


@traceable(run_type="llm", name="sufficiency_check")
def assess_sufficiency(
    *, question: str, chunks: list[RetrievedChunk]
) -> SufficiencyVerdict:
    """Prompt Gemini to rate (question, chunks) for sufficient context.
    Returns a normalized verdict. Fail-open: any error → sufficient=true
    so we never block the answer on the rater being unavailable."""
    if not chunks:
        # Nothing retrieved → the agent either decided no retrieval was
        # needed (smalltalk, scope-fallback) or had no tools fire. Don't
        # second-guess — sufficient by default.
        return {"sufficient": True, "missing": None, "feedback": None}

    user_text = (
        f"Nutzerfrage:\n{question}\n\n"
        f"Retrieved chunks:\n{_format_chunks_for_rater(chunks)}"
    )

    try:
        resp = gemini_client().chat.completions.create(
            model=settings.gemini_chat_model,
            messages=[
                {"role": "system", "content": _INSTRUCTION},
                {"role": "user", "content": user_text},
            ],
            response_format={"type": "json_object"},
            extra_body={"reasoning_effort": "none"},
        )
    except Exception as exc:
        log.warning("sufficiency_check: gemini call failed: %s", exc)
        return {"sufficient": True, "missing": None, "feedback": None}

    raw = (resp.choices[0].message.content or "").strip()
    if not raw:
        return {"sufficient": True, "missing": None, "feedback": None}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("sufficiency_check: non-JSON response: %r", raw[:200])
        return {"sufficient": True, "missing": None, "feedback": None}

    sufficient = bool(parsed.get("sufficient", True))
    missing = parsed.get("missing") if not sufficient else None
    feedback = parsed.get("feedback") if not sufficient else None
    return {
        "sufficient": sufficient,
        "missing": missing if isinstance(missing, str) else None,
        "feedback": feedback if isinstance(feedback, str) else None,
    }


def build_continuation_hint(verdict: SufficiencyVerdict) -> str:
    """Format an insufficient verdict as a system message that nudges the
    agent into one more retrieval round. Caller should append this to
    `messages` between the assistant's would-be-final iteration and the
    next loop iteration."""
    parts = [
        "SUFFICIENCY-CHECK: deine bisherige Antwort deckt die Frage "
        "möglicherweise nicht vollständig ab."
    ]
    if verdict.get("missing"):
        parts.append(f"Fehlt: {verdict['missing']}")
    if verdict.get("feedback"):
        parts.append(f"Vorschlag: {verdict['feedback']}")
    parts.append(
        "Mache einen weiteren gezielten Retrieval-Tool-Aufruf "
        "(`search_chunks` mit anderem Suchbegriff, "
        "`list_document_outline` auf einer wahrscheinlichen Datei, oder "
        "`read_section` auf der vermuteten Stelle) und antworte dann."
    )
    return " ".join(parts)
