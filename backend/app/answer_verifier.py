"""Answer-correctness verifier — second-pass autorater (plan 17.4.1 G4).

Sufficiency rates COVERAGE; this rates CORRECTNESS. Catches the case
where retrieved chunks contain the right evidence but the model
paraphrased or interpreted them wrong (e.g., Q10: chunks say "X is to be
coordinated by the offerer, executed by separate specialists" → model
writes "X is part of the offerer's contract").

Wired into the chat agent loop after sufficiency=true and before
streaming the final text. Triggered only on
`question_type ∈ {point, aggregation, total}` (the types where
inversion / contradiction / fabrication is possible). Skipped on
`phrase` / `out_of_scope` / no chunks.

Cost: one Gemini call per chat turn that passes the sufficiency gate
(~500ms, ~3k tokens). Skipped if no chunks were retrieved.

Fail-open: any Gemini error, non-JSON response, or parse failure →
ok=true, so the verifier never blocks the answer when upstream is
flaky.
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


class VerifierVerdict(TypedDict):
    ok: bool
    issue: str | None
    fix: str | None


_VERIFIER_INSTRUCTION = (
    "Du bist ein Korrektheits-Verifier für ein RAG-System. Du bekommst "
    "(1) eine Nutzerfrage, (2) den ENTWURF einer Assistenten-Antwort, "
    "(3) die Chunks (mit Excerpts), die der Assistent zur Erzeugung des "
    "Entwurfs gesehen hat. Deine Aufgabe: prüfe, ob der Entwurf das, was "
    "in den Chunks steht, KORREKT wiedergibt.\n\n"
    "PRÜFE GENAU DIESE FAILURE-MODES:\n"
    "1. INVERSION: der Entwurf behauptet das Gegenteil dessen, was in "
    "   den Chunks steht (z.B. 'Vermessung ist Teil des Auftrags' wenn "
    "   die Chunks sagen 'Vermessung wird in separaten Mandaten "
    "   vergeben und ist vom Anbieter nur zu koordinieren').\n"
    "2. FABRIKATION: der Entwurf nennt einen Wert/Fakt, der in den "
    "   Chunks NICHT steht, insbesondere bei Summen, Totals, Stunden-"
    "   zahlen.\n"
    "3. ENTITY-MIX-UP: der Entwurf weist eine Aussage der falschen "
    "   Person/Firma/Phase zu (z.B. 'TP1-Leiter Kieliger' wenn die "
    "   Chunks Kieliger als TP2-Leiter belegen).\n"
    "4. SCOPE-FEHLER: der Entwurf antwortet 'nicht gefunden' obwohl ein "
    "   Chunk die Antwort enthält, oder umgekehrt.\n\n"
    "Du sollst NICHT nach Vollständigkeit prüfen — das macht ein anderer "
    "Rater. Nur KORREKTHEIT der vorhandenen Aussagen.\n\n"
    "Bei `ok=false` MUSST du in `issue` (≤1 Satz) den konkreten "
    "Widerspruch nennen und in `fix` (≤1 Satz) sagen, wie die Antwort "
    "korrigiert werden soll (z.B. 'Antworte: Vermessung ist NICHT Teil "
    "des Auftrags, sondern in separaten Mandaten an Spezialisten "
    "vergeben').\n\n"
    "ANTWORTE AUSSCHLIESSLICH MIT JSON:\n"
    '{"ok": true|false, "issue": "..." oder null, "fix": "..." oder null}'
)


_VERIFIER_EXCERPT_CHARS = 600
_VERIFIER_MAX_CHUNKS = 30


def _format_chunks_for_verifier(chunks: list[RetrievedChunk]) -> str:
    """Same shape as the sufficiency rater's renderer — heading, page,
    block_type, then 600 chars of content. Kept local instead of imported
    to avoid coupling the verifier to sufficiency's internal helper."""
    if not chunks:
        return "(Keine Chunks retrieved.)"
    lines: list[str] = []
    for i, c in enumerate(chunks[:_VERIFIER_MAX_CHUNKS], 1):
        excerpt = c.content[:_VERIFIER_EXCERPT_CHARS]
        if len(c.content) > _VERIFIER_EXCERPT_CHARS:
            excerpt += "…"
        prefix = c.file_id.replace("-", "")[:8] if c.file_id else "?"
        heading = " > ".join(c.heading_path[:4]) if c.heading_path else "-"
        figure = c.figure_label or "-"
        head = (
            f"[{i}] {c.filename}\n"
            f"    file_id: {prefix}\n"
            f"    page: {c.page_start}"
            + (f"-{c.page_end}" if c.page_end != c.page_start else "")
            + "\n"
            f"    block_type: {c.block_type}\n"
            f"    heading_path: {heading}\n"
            f"    figure_label: {figure}\n"
            f"    excerpt:"
        )
        lines.append(f"{head}\n{excerpt}")
    if len(chunks) > _VERIFIER_MAX_CHUNKS:
        lines.append(
            f"…(+{len(chunks) - _VERIFIER_MAX_CHUNKS} weitere Chunks ausgelassen)"
        )
    return "\n\n".join(lines)


@traceable(run_type="llm", name="answer_verifier")
def verify_answer(
    *,
    question: str,
    draft: str,
    chunks: list[RetrievedChunk],
    question_type: str | None = None,
) -> VerifierVerdict:
    """Returns ok=true if the draft accurately reflects the chunks. Fail-
    open on Gemini errors (we don't want the verifier blocking answers
    when upstream is flaky)."""
    if not chunks or not draft.strip():
        return {"ok": True, "issue": None, "fix": None}
    if question_type in {"phrase", "out_of_scope"}:
        return {"ok": True, "issue": None, "fix": None}

    user_text = (
        f"Nutzerfrage:\n{question}\n\n"
        f"Entwurf-Antwort:\n{draft}\n\n"
        f"Retrieved chunks:\n{_format_chunks_for_verifier(chunks)}"
    )
    try:
        resp = gemini_client().chat.completions.create(
            model=settings.gemini_chat_model,
            messages=[
                {"role": "system", "content": _VERIFIER_INSTRUCTION},
                {"role": "user", "content": user_text},
            ],
            response_format={"type": "json_object"},
            extra_body={"reasoning_effort": "none"},
        )
    except Exception as exc:
        log.warning("answer_verifier: gemini call failed: %s", exc)
        return {"ok": True, "issue": None, "fix": None}

    raw = (resp.choices[0].message.content or "").strip()
    if not raw:
        return {"ok": True, "issue": None, "fix": None}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("answer_verifier: non-JSON response: %r", raw[:200])
        return {"ok": True, "issue": None, "fix": None}

    ok = bool(parsed.get("ok", True))
    issue = parsed.get("issue") if not ok else None
    fix = parsed.get("fix") if not ok else None
    log.info(
        "answer_verifier verdict: ok=%s issue=%r",
        ok,
        (issue if isinstance(issue, str) else "")[:120],
    )
    return {
        "ok": ok,
        "issue": issue if isinstance(issue, str) else None,
        "fix": fix if isinstance(fix, str) else None,
    }


def build_verifier_correction_hint(verdict: VerifierVerdict) -> str:
    """Format an `ok=false` verdict as a system-message correction hint
    appended to the message history before the next iteration."""
    parts = [
        "KORREKTHEITS-CHECK: dein Antwort-Entwurf scheint die Chunks "
        "falsch wiederzugeben."
    ]
    if verdict.get("issue"):
        parts.append(f"Problem: {verdict['issue']}")
    if verdict.get("fix"):
        parts.append(f"Korrektur: {verdict['fix']}")
    parts.append(
        "Formuliere die Antwort neu — ZITAT-PFLICHT: gib die belegende "
        "Stelle in deiner neuen Antwort wörtlich (oder als nahes "
        "Paraphrase) wieder, damit der Bezug zu den Chunks eindeutig ist."
    )
    return " ".join(parts)
