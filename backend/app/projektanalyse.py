"""Projektanalyse v2 batch handler — RAG-specialist fan-out edition.

Triggered when the chat orchestrator calls run_projektanalyse_v2.

v2 (post-2026-05-02 rewrite): the user-supplied template is fanned out
across rag_specialist (Vertex RAG corpus, native grounding, full SIA
instruction) instead of stuffing document_chunks into a single Gemini
call. Empirically (see backend/scripts/test_batched_rag_recall.py) this
recovers facts that the previous batched-prompt approach lost — most
notably the Bausumme — while reusing the same per-question instruction
the chat path uses.

Concurrency cap keeps wallclock predictable: 11 simultaneous rag_specialist
calls saturate the DSQ pool and serialise through ADK retry backoff
(~608s). DISPATCH_CONCURRENCY=4 keeps it to ~3 batches × ~19s.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import AsyncGenerator

from langsmith import traceable

from app.adk.agents import make_rag_specialist
from app.adk.dispatch_rag_questions_tool import (
    DISPATCH_CONCURRENCY,
    _run_one_rag_specialist,
)
from app.db import supabase

log = logging.getLogger(__name__)


def _gemini_error_placeholder(exc: Exception) -> str:  # noqa: ARG001 — kept for log parity at call site
    """Vendor-neutral per-question fallback. A single upstream failure must
    not kill a multi-question report — the placeholder keeps the question's
    slot in the final markdown so the user can re-run just that one. Provider
    name and status code stay in the backend log only."""
    return "_⚠️ Antwort konnte nicht erzeugt werden — bitte Frage erneut stellen._"


# Plan 19.0 T11: Pattern A FunctionDeclarations for v1/v2 are gone — the
# orchestrator owns its own ADK FunctionTool for v2 (app/projektanalyse_v2_tool.py)
# and v1 is no longer in any tool list. v2 is still wired to the
# stream_projektanalyse_v2 streamer below via the chat handler's hand-off.


# Kept as legacy/reference: the v2 (Volltext) instruction the previous
# document_chunks-based path used. The new RAG-specialist fan-out path uses
# RAG_SPECIALIST_INSTRUCTION (per-call, native grounding) instead — same
# domain rules (SIA scope-fallback, no-self-sum, ROLLEN-FRAGEN), authored once
# in app/adk/instructions.py and reused. Kept here only to make the diff
# auditable; the constant is no longer consumed.
ANSWER_INSTRUCTIONS = (
    "Du beantwortest eine einzelne Frage einer strukturierten Projektanalyse "
    "für ein Schweizer Bahn-/Ingenieurprojekt. Deine Antwort wird unverändert "
    "unter die Frage in einen Bericht übernommen.\n\n"
    "REGELN:\n"
    "1. Antworte direkt und faktenorientiert. Kein Vorgeplänkel, keine "
    "Wiederholung der Frage, keine Floskeln wie 'Gemäß den Dokumenten…'.\n"
    "2. Extrahiere konkrete Werte aus den Dokumenten — Phasen (z.B. SIA 31, "
    "32, 41), Namen, Firmen, Termine, Beträge in CHF, Stundenzahlen, "
    "Meilensteine. Zitiere kurze Schlüsselstellen wörtlich in "
    "Anführungszeichen.\n"
    "3. Format passt zur Frage:\n"
    "   - 'Was/Wer/Wie heisst…?' → ein Wert oder kurzer Satz.\n"
    "   - 'Welche…?' → Aufzählungsliste (Markdown-Bullets).\n"
    "   - 'Ist X Bestandteil…?' / 'Steht X in den Plänen?' → 'Ja' oder "
    "'Nein' plus ein Satz Beleg, gerne mit wörtlichem Zitat.\n"
    "   - Fragen nach Summen / Bausumme / Gesamtkosten / Honorar / "
    "Gesamtaufwand: IMMER zuerst den Gesamtwert (Headline) nennen, "
    "DANN die vollständige Aufteilung (z.B. nach Etappen, Phasen, "
    "Modulen, Fachdisziplinen) als Bullet-Liste mit den jeweiligen "
    "Beträgen. Wenn beides in den Dokumenten vorhanden ist, BEIDES "
    "ausgeben — nie nur die Aufteilung ohne Total und nie nur das Total "
    "ohne Aufteilung.\n"
    "4. WENN DIE FRAGE OFFEN FORMULIERT IST (z.B. 'oder etwas ähnliches', "
    "'oder vergleichbare', 'etc.', 'ähnliche Hinweise'), suche nach allen "
    "sinnverwandten Stellen — nicht nur nach exakten Wortlauten. Liste "
    "jeden Treffer mit kurzem wörtlichem Zitat und Fundstelle (z.B. "
    "Kapitel/Abbildung/Tabelle) auf.\n"
    "5. PFLICHT-PRÜFUNG VOR 'Nicht in den Dokumenten gefunden': Bevor du "
    "diese Phrase verwendest, prüfe explizit, ob das Thema der Frage "
    "außerhalb des in den Dokumenten beschriebenen Auftragsumfangs liegt. "
    "Wichtigste Heuristik für Schweizer Bahn-/Ingenieurprojekte:\n"
    "   - Wenn die Beschaffung nur die SIA-Phasen 21 (Machbarkeitsstudie) "
    "und/oder 31 (Vorprojekt plus) umfasst, dann fallen Fragen zum "
    "BAUPROJEKT (SIA 32/41) oder zum AUSFÜHRUNGSPROJEKT (SIA 51+) "
    "DEFINITIV NICHT unter diese Beschaffung — auch wenn die Dokumente "
    "dazu kein Wort verlieren. Das ist KEIN 'Nicht gefunden'-Fall, "
    "sondern ein Scope-Fall.\n"
    "   In solchen Fällen antworte: 'Nicht Teil dieser Beschaffung — der "
    "Auftragsumfang umfasst nur [konkrete Phasen/Bereich]. [Ein Satz Beleg "
    "aus den Dokumenten, der den Scope bestätigt.]'\n"
    "6. Wenn die Antwort nicht eindeutig in den Dokumenten steht UND die "
    "Frage nicht unter Regel 5 fällt, schreibe GENAU:\n"
    "   **Nicht in den Dokumenten gefunden.**\n"
    "7. Wenn nur Teilinformationen vorhanden sind, gib das Vorhandene "
    "konkret an und vermerke in einem kurzen Satz, was fehlt.\n"
    "8. Antworte auf Deutsch.\n"
    "9. Bei Total-/Summen-Fragen (Bausumme, Gesamtkosten, Gesamtaufwand, "
    "Stunden insgesamt): Du darfst NIEMALS Teilbeträge selbst summieren, "
    "um einen Gesamtwert zu erzeugen. Wenn der Headline-/Total-Wert "
    "nicht explizit im Kontext steht, antworte: \"Der Gesamt-/Headline-"
    "Wert ist in den abgerufenen Chunks nicht explizit enthalten. Die "
    "einzelnen Teilbeträge: …\" und liste die Teilbeträge auf — auch im "
    "Projektanalyse-Tool-Output: keine Selbst-Summierung.\n"
    "10. Bei Rollen-Fragen (\"wer ist der Projektleiter / Verantwortliche "
    "/ Ansprechpartner / Bauherr\"): die Dokumente betreffen ein Tender-"
    "Projekt vor Auftragsvergabe; die anbieter-seitigen Personen sind "
    "typischerweise NICHT benannt. Antworte mit allen Personen aus den "
    "Dokumenten, die zur Rollen-Familie passen (Projektleiter, Teil-"
    "projektleiter, Projektkoordinator), MIT Rollen-Bezeichnung und "
    "Fundstelle. Verweigere nur, wenn keine passende Person belegt ist."
)


# --- helpers ---


def _project_id_for_chat(chat_id: str, user_id: str) -> str | None:
    res = (
        supabase()
        .table("chats")
        .select("project_id")
        .eq("id", chat_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    return (res.data or {}).get("project_id")


def _load_corpus_name(project_id: str) -> str | None:
    """Resolve the Vertex RAG corpus resource name for a project.

    Inlined here (rather than imported from app.routers.chats) to avoid a
    circular dependency: chats.py imports stream_projektanalyse_v2, so
    projektanalyse.py cannot import from chats.py.
    """
    row = (
        supabase()
        .table("projects")
        .select("rag_corpus_name")
        .eq("id", project_id)
        .single()
        .execute()
    )
    return (row.data or {}).get("rag_corpus_name")


@traceable(run_type="llm", name="projektanalyse_v2.answer_one")
async def _answer_v2_via_rag(question: str, *, rag_specialist, user_id: str) -> str:
    """Run a single template question through rag_specialist.

    Replaces the previous v2 path that stuffed document_chunks into a single
    Gemini call — that path is broken on serverless corpora (document_chunks
    is empty) and degrades on multi-fact questions even when chunks exist.
    Native vertex_rag_store grounding inside rag_specialist now does the
    retrieval per question.
    """
    try:
        text, _gm = await _run_one_rag_specialist(
            rag_specialist, question, user_id=user_id
        )
    except Exception as exc:
        log.warning("projektanalyse v2 question failed: %s", exc)
        return _gemini_error_placeholder(exc)
    return text or "_(keine Antwort)_"


def _assemble_report(
    questions: list[str], answers: list[str], *, title: str = "Projektanalyse"
) -> str:
    parts = [f"# {title}\n"]
    for i, (q, a) in enumerate(zip(questions, answers), 1):
        parts.append(f"## {i}. {q}\n\n{a}\n")
    return "\n".join(parts)


@traceable(run_type="chain", name="projektanalyse.run")
async def _run_batch(
    *, questions: list[str], answer_fn, concurrency: int | None = None
) -> AsyncGenerator[tuple[str, dict], None]:
    """Fan out questions across answer_fn with progress events.

    answer_fn may be sync (wrapped via to_thread) or async (awaited directly).
    `concurrency` caps the number of in-flight calls; defaults to unbounded
    for backwards compatibility. The new RAG-backed v2 passes
    DISPATCH_CONCURRENCY to avoid saturating the Vertex DSQ pool.
    """
    total = len(questions)
    answers: list[str] = [""] * total
    sem = asyncio.Semaphore(concurrency) if concurrency else None
    is_async = inspect.iscoroutinefunction(answer_fn)

    async def _run(idx: int, q: str) -> tuple[int, str]:
        async def _do() -> str:
            if is_async:
                return await answer_fn(q)
            return await asyncio.to_thread(answer_fn, q)

        if sem is None:
            ans = await _do()
        else:
            async with sem:
                ans = await _do()
        return idx, ans

    tasks = [asyncio.create_task(_run(i, q)) for i, q in enumerate(questions)]
    done = 0
    for fut in asyncio.as_completed(tasks):
        idx, ans = await fut
        answers[idx] = ans
        done += 1
        yield (
            "progress",
            {"done": done, "total": total, "question": questions[idx]},
        )

    yield ("report", {"questions": questions, "answers": answers})


def _persist_assistant_message(*, chat_id: str, user_id: str, content: str) -> str | None:
    if not content:
        return None
    try:
        ins = (
            supabase()
            .table("chat_messages")
            .insert(
                {
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "role": "assistant",
                    "content": content,
                    "tool_name": "projektanalyse",
                }
            )
            .execute()
        )
        return ins.data[0]["id"] if ins.data else None
    except Exception:
        return None


async def _stream_common(
    *,
    template: list[str] | None,
    answer_fn,
    title: str,
    chat_id: str,
    user_id: str,
    no_input_msg: str,
    concurrency: int | None = None,
) -> AsyncGenerator[str, None]:
    questions = [q.strip() for q in (template or []) if q and q.strip()]
    total = len(questions)

    if total == 0:
        msg = "_(Keine Vorlage hinterlegt — bitte Projektanalyse-Vorlage ausfüllen.)_"
        yield f"data: {json.dumps({'type': 'delta', 'content': msg})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return

    if answer_fn is None:
        yield f"data: {json.dumps({'type': 'delta', 'content': no_input_msg})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return

    yield f"data: {json.dumps({'progress': {'done': 0, 'total': total}})}\n\n"

    report = ""
    async for kind, payload in _run_batch(
        questions=questions, answer_fn=answer_fn, concurrency=concurrency
    ):
        if kind == "progress":
            yield f"data: {json.dumps({'progress': payload})}\n\n"
        elif kind == "report":
            report = _assemble_report(
                payload["questions"], payload["answers"], title=title
            )

    msg_id = await asyncio.to_thread(
        _persist_assistant_message, chat_id=chat_id, user_id=user_id, content=report
    )

    yield f"data: {json.dumps({'type': 'delta', 'content': report})}\n\n"
    done_payload: dict = {"type": "done"}
    if msg_id:
        done_payload["message_id"] = msg_id
    yield f"data: {json.dumps(done_payload)}\n\n"


async def stream_projektanalyse_v2(
    *, template: list[str] | None, chat_id: str, user_id: str
) -> AsyncGenerator[str, None]:
    """v2: per-question rag_specialist fan-out (Vertex RAG corpus, native grounding).

    Each template question runs through a fresh rag_specialist Runner with
    its own retrieval window (top_k=10 per call, see app/adk/agents.py).
    Concurrency capped at DISPATCH_CONCURRENCY to avoid DSQ pool saturation.
    """
    title = "Projektanalyse"
    project_id = await asyncio.to_thread(_project_id_for_chat, chat_id, user_id)
    if not project_id:
        async for sse in _stream_common(
            template=template,
            answer_fn=None,
            title=title,
            chat_id=chat_id,
            user_id=user_id,
            no_input_msg="_(Projekt nicht gefunden — Vorlage konnte nicht beantwortet werden.)_",
        ):
            yield sse
        return

    corpus_name = await asyncio.to_thread(_load_corpus_name, project_id)
    if not corpus_name:
        async for sse in _stream_common(
            template=template,
            answer_fn=None,
            title=title,
            chat_id=chat_id,
            user_id=user_id,
            no_input_msg="_(Keine Projektdokumente vorhanden — Vorlage konnte nicht beantwortet werden.)_",
        ):
            yield sse
        return

    rag_specialist = make_rag_specialist(corpus_name)

    async def answer_fn(q: str) -> str:
        return await _answer_v2_via_rag(
            q, rag_specialist=rag_specialist, user_id=user_id
        )

    async for sse in _stream_common(
        template=template,
        answer_fn=answer_fn,
        title=title,
        chat_id=chat_id,
        user_id=user_id,
        no_input_msg="_(Keine Projektdokumente vorhanden — Vorlage konnte nicht beantwortet werden.)_",
        concurrency=DISPATCH_CONCURRENCY,
    ):
        yield sse
