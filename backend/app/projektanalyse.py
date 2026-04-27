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


PROJEKTANALYSE_V2_TOOL = {
    "type": "function",
    "name": "run_projektanalyse_v2",
    "description": (
        "Führt die strukturierte Projektanalyse v2 (Volltext-Modus) aus — "
        "die Projektdokumente werden komplett in den Modell-Kontext geladen "
        "(kein file_search), Antworten sind dadurch vollständiger. "
        "RUFE DIESES TOOL AUF, wenn der Nutzer explizit 'Projektanalyse v2', "
        "'v2 Analyse' oder 'Volltext-Analyse' anfordert. Keine Argumente nötig."
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}


PROJEKTANALYSE_INSTRUCTIONS = (
    "Wenn du eines der Tools `run_projektanalyse` oder `run_projektanalyse_v2` "
    "aufrufst, gib das Tool-Ergebnis exakt und vollständig als deine Antwort "
    "aus. Keine Einleitung, keine Zusammenfassung, kein zusätzlicher "
    "Kommentar — nur das Tool-Resultat."
)


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
    "Kapitel/Abbildung/Tabelle) auf. Beispiele für sinnverwandte "
    "Hinweise auf 'in einer späteren Phase zu detaillieren': 'wird in "
    "Phase X bearbeitet', 'noch zu definieren', 'noch in Ausarbeitung', "
    "'Platzhalter', 'definitive Ausführung erfolgt später', 'siehe spätere "
    "Etappe'.\n"
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
    "   Keine Definition des Begriffs, keine Mutmaßungen, keine Verweise auf "
    "'siehe Abschnitt X' oder 'weitere Details finden Sie in …' — der "
    "Bericht hat keine solchen Abschnitte.\n"
    "7. Wenn nur Teilinformationen vorhanden sind, gib das Vorhandene "
    "konkret an und vermerke in einem kurzen Satz, was fehlt.\n"
    "8. Antworte auf Deutsch."
)


@traceable(run_type="llm", name="projektanalyse.answer_one")
def _answer_one_sync(question: str, vector_store_id: str) -> str:
    resp = openai_client().responses.create(
        model="gpt-4o",
        instructions=ANSWER_INSTRUCTIONS,
        input=[{"role": "user", "content": question}],
        tools=[
            {
                "type": "file_search",
                "vector_store_ids": [vector_store_id],
                "max_num_results": 30,
            }
        ],
        include=["file_search_call.results"],
    )
    return resp.output_text or "_(keine Antwort)_"


def _assemble_report(questions: list[str], answers: list[str], *, title: str = "Projektanalyse") -> str:
    parts = [f"# {title}\n"]
    for i, (q, a) in enumerate(zip(questions, answers), 1):
        parts.append(f"## {i}. {q}\n\n{a}\n")
    return "\n".join(parts)


@traceable(run_type="llm", name="projektanalyse_v2.answer_one")
def _answer_one_v2_sync(question: str, file_ids: list[str]) -> str:
    """Volltext-Modus: send all project files as input_file blocks instead of
    relying on file_search retrieval. Each file is read end-to-end by the model
    (PDF/DOCX extracted natively). Prompt caching kicks in automatically across
    parallel calls — the file blocks are identical so OpenAI caches the prefix
    and we only pay full price for the first call."""
    file_blocks = [{"type": "input_file", "file_id": fid} for fid in file_ids]
    resp = openai_client().responses.create(
        model="gpt-4.1",
        instructions=ANSWER_INSTRUCTIONS,
        input=[
            {
                "role": "user",
                "content": [
                    *file_blocks,
                    {"type": "input_text", "text": question},
                ],
            }
        ],
    )
    usage = getattr(resp, "usage", None)
    if usage is not None:
        in_tok = getattr(usage, "input_tokens", "?")
        out_tok = getattr(usage, "output_tokens", "?")
        cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", "?")
        print(
            f"[projektanalyse v2] files={len(file_ids)} "
            f"input_tokens={in_tok} cached={cached} output_tokens={out_tok} "
            f"q={question[:60]!r}",
            flush=True,
        )
    return resp.output_text or "_(keine Antwort)_"


@traceable(run_type="chain", name="projektanalyse.run")
async def _run_batch(
    *,
    questions: list[str],
    answer_fn,
) -> AsyncGenerator[tuple[str, dict], None]:
    """Yield ('progress', {...}) events as each question completes, then a
    final ('report', {'text': <markdown>}) event. answer_fn is a sync callable
    that takes a single question string and returns the answer string."""
    total = len(questions)
    answers: list[str] = [""] * total

    async def _run(idx: int, q: str) -> tuple[int, str]:
        ans = await asyncio.to_thread(answer_fn, q)
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


async def _stream_common(
    *,
    template: list[str] | None,
    answer_fn,
    title: str,
    conversation_id: str | None,
    no_input_msg: str,
) -> AsyncGenerator[str, None]:
    questions = [q.strip() for q in (template or []) if q and q.strip()]
    total = len(questions)

    if total == 0:
        msg = "_(Keine Vorlage hinterlegt — bitte Projektanalyse-Vorlage ausfüllen.)_"
        yield f"data: {json.dumps({'delta': msg})}\n\n"
        yield "data: [DONE]\n\n"
        return

    if answer_fn is None:
        yield f"data: {json.dumps({'delta': no_input_msg})}\n\n"
        yield "data: [DONE]\n\n"
        return

    yield f"data: {json.dumps({'progress': {'done': 0, 'total': total}})}\n\n"

    report = ""
    async for kind, payload in _run_batch(questions=questions, answer_fn=answer_fn):
        if kind == "progress":
            yield f"data: {json.dumps({'progress': payload})}\n\n"
        elif kind == "report":
            report = _assemble_report(payload["questions"], payload["answers"], title=title)

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
            pass

    yield f"data: {json.dumps({'delta': report})}\n\n"
    yield "data: [DONE]\n\n"


async def stream_projektanalyse(
    *,
    template: list[str] | None,
    vector_store_id: str | None,
    conversation_id: str | None,
) -> AsyncGenerator[str, None]:
    """v1: file_search per question."""
    answer_fn = (
        (lambda q: _answer_one_sync(q, vector_store_id))
        if vector_store_id
        else None
    )
    async for sse in _stream_common(
        template=template,
        answer_fn=answer_fn,
        title="Projektanalyse",
        conversation_id=conversation_id,
        no_input_msg="_(Keine Projektdateien vorhanden — Vorlage konnte nicht beantwortet werden.)_",
    ):
        yield sse


async def stream_projektanalyse_v2(
    *,
    template: list[str] | None,
    file_ids: list[str],
    conversation_id: str | None,
) -> AsyncGenerator[str, None]:
    """v2: full-document context per question (no retrieval)."""
    answer_fn = (
        (lambda q: _answer_one_v2_sync(q, file_ids)) if file_ids else None
    )
    async for sse in _stream_common(
        template=template,
        answer_fn=answer_fn,
        title="Projektanalyse v2 (Volltext)",
        conversation_id=conversation_id,
        no_input_msg="_(Keine Projektdateien vorhanden — Vorlage konnte nicht beantwortet werden.)_",
    ):
        yield sse
