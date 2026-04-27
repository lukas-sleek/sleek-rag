"""Projektanalyse batch handler.

Triggered when the LLM calls the `run_projektanalyse` function tool. Fans out
N parallel `responses.create` calls (one per template question) against the
project's vector store, assembles the results into a markdown report, and
streams progress events back over SSE.
"""

import asyncio
import json
from typing import AsyncGenerator

from langsmith import traceable

from app.openai_client import openai_client


PROJEKTANALYSE_TOOL = {
    "type": "function",
    "name": "run_projektanalyse",
    "description": (
        "Führt die strukturierte Projektanalyse für das aktive Projekt aus. "
        "Beantwortet alle in der Nutzer-Vorlage hinterlegten Fragen parallel "
        "anhand der hochgeladenen Projektdokumente und liefert einen "
        "formatierten Bericht zurück. RUFE DIESES TOOL AUF, wenn der Nutzer "
        "eine Projektanalyse anfordert — z.B. 'erstelle mir eine Projektanalyse', "
        "'Projektanalyse erstellen', 'mach mal ne Analyse', 'projektanalys', "
        "'kannst du das Projekt analysieren'. Keine Argumente nötig — die "
        "Vorlage und das aktive Projekt werden serverseitig aufgelöst."
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}


PROJEKTANALYSE_INSTRUCTIONS = (
    "Wenn du das Tool `run_projektanalyse` aufrufst, gib das Tool-Ergebnis "
    "exakt und vollständig als deine Antwort aus. Keine Einleitung, keine "
    "Zusammenfassung, kein zusätzlicher Kommentar — nur das Tool-Resultat."
)


@traceable(run_type="llm", name="projektanalyse.answer_one")
def _answer_one_sync(question: str, vector_store_id: str) -> str:
    resp = openai_client().responses.create(
        model="gpt-4o-mini",
        input=[{"role": "user", "content": question}],
        tools=[{"type": "file_search", "vector_store_ids": [vector_store_id]}],
        include=["file_search_call.results"],
    )
    return resp.output_text or "_(keine Antwort)_"


def _assemble_report(questions: list[str], answers: list[str]) -> str:
    parts = ["# Projektanalyse\n"]
    for i, (q, a) in enumerate(zip(questions, answers), 1):
        parts.append(f"## {i}. {q}\n\n{a}\n")
    return "\n".join(parts)


@traceable(run_type="chain", name="projektanalyse.run")
async def _run_batch(
    *, questions: list[str], vector_store_id: str
) -> AsyncGenerator[tuple[str, dict], None]:
    """Yield ('progress', {...}) events as each question completes, then a
    final ('report', {'text': <markdown>}) event."""
    total = len(questions)
    answers: list[str] = [""] * total

    async def _run(idx: int, q: str) -> tuple[int, str]:
        ans = await asyncio.to_thread(_answer_one_sync, q, vector_store_id)
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

    yield ("report", {"text": _assemble_report(questions, answers)})


async def stream_projektanalyse(
    *,
    template: list[str] | None,
    vector_store_id: str | None,
    conversation_id: str | None,
) -> AsyncGenerator[str, None]:
    """SSE generator for a Projektanalyse run. Emits progress events, the
    final report as a single delta, persists the report into the OpenAI
    conversation thread, and terminates with [DONE]."""
    questions = [q.strip() for q in (template or []) if q and q.strip()]
    total = len(questions)

    if total == 0:
        msg = "_(Keine Vorlage hinterlegt — bitte Projektanalyse-Vorlage ausfüllen.)_"
        yield f"data: {json.dumps({'delta': msg})}\n\n"
        yield "data: [DONE]\n\n"
        return

    if not vector_store_id:
        msg = "_(Keine Projektdateien vorhanden — Vorlage konnte nicht beantwortet werden.)_"
        yield f"data: {json.dumps({'delta': msg})}\n\n"
        yield "data: [DONE]\n\n"
        return

    yield f"data: {json.dumps({'progress': {'done': 0, 'total': total}})}\n\n"

    report = ""
    async for kind, payload in _run_batch(
        questions=questions, vector_store_id=vector_store_id
    ):
        if kind == "progress":
            yield f"data: {json.dumps({'progress': payload})}\n\n"
        elif kind == "report":
            report = payload["text"]

    if conversation_id and report:
        try:
            await asyncio.to_thread(
                openai_client().conversations.items.create,
                conversation_id=conversation_id,
                items=[
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": report}],
                    }
                ],
            )
        except Exception:
            # Persistence is best-effort — the user still sees the report
            # via the streamed delta below. Worst case: refresh loses it.
            pass

    yield f"data: {json.dumps({'delta': report})}\n\n"
    yield "data: [DONE]\n\n"
