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
    # Plan 17.4.1 G4: surfaced so the answer-correctness verifier can skip
    # phrase / out_of_scope turns. Optional via NotRequired-equivalent — the
    # field is always present but may be None when the rater short-circuited
    # (no chunks) or returned a non-JSON response.
    question_type: str | None


_INSTRUCTION = (
    "Du bist ein Sufficient-Context-Rater für ein RAG-System für "
    "Schweizer Bahn-/Ingenieurprojekt-Ausschreibungen. Du bekommst "
    "(1) eine Nutzerfrage und (2) eine Liste von Chunk-Excerpts mit "
    "Datei-/Seiten-/Heading-Metadaten. Deine Aufgabe: entscheide, ob "
    "die Chunks ausreichen, um die Nutzerfrage VOLLSTÄNDIG und ohne "
    "Lücken zu beantworten.\n\n"
    "SCHRITT 1 — KLASSIFIZIERE DEN FRAGENTYP:\n\n"
    "Wende diese strukturellen Cues ZUERST an. Sie überschreiben deine "
    "Intuition über den Inhalt der Chunks:\n\n"
    "CUE-A (Aggregation, hart): wenn die Frage eines dieser Muster "
    "enthält, ist der Typ IMMER `aggregation`, unabhängig davon wieviele "
    "Entitäten in den Chunks stehen:\n"
    "  • \"welche [Plural-Substantiv]\" (welche Bauherren, welche "
    "Termine, welche Drittprojekte, welche Elemente, welche Mandate)\n"
    "  • \"alle [Substantiv]\"\n"
    "  • \"wer ist beteiligt\", \"wer sind die Beteiligten\"\n"
    "  • \"Liste aller …\", \"auflisten\"\n"
    "  • Substantiv im Plural mit bestimmtem Artikel (\"die "
    "Beteiligten\", \"die Termine\")\n\n"
    "CUE-B (Total, hart): wenn die Frage einen Headline-/Gesamtwert "
    "verlangt, ist der Typ IMMER `total`:\n"
    "  • \"Bausumme\", \"Gesamtkosten\", \"Gesamtaufwand\"\n"
    "  • \"wie viele Stunden insgesamt\", \"Gesamtanzahl\"\n"
    "  • \"Total\", \"Gesamttermin\", \"Endsumme\"\n"
    "  • \"Gesamtprojektsumme\"\n\n"
    "CUE-C (Phrase, hart): wenn die Frage explizit nach einer Wortlaut-"
    "Suche fragt:\n"
    "  • \"steht der Satz / der Kommentar / die Phrase X in den "
    "Plänen/Dokumenten?\"\n"
    "  • Anführungszeichen um eine Mehrwort-Phrase\n"
    "  • \"wird Y wörtlich erwähnt?\"\n\n"
    "CUE-D (Out-of-scope, hart): wenn die Frage erkennbar SIA-Phasen "
    "ausserhalb 21/31 betrifft:\n"
    "  • \"Bauprojekt\", \"Ausführung\", \"Ausführungsprojekt\"\n"
    "  • \"SIA 32\", \"SIA 33\", \"SIA 41\", \"SIA 51\"\n\n"
    "Erst wenn KEINER dieser Cues greift, klassifiziere als `point` "
    "(Einzelfakt-Frage).\n\n"
    "Hinweis: bei \"Welche Bauherren sind beteiligt?\" greift CUE-A "
    "(welche + Plural Bauherren) → IMMER aggregation, auch wenn nur 1 "
    "Entität in den Chunks ist. Bei out_of_scope-Konflikten gewinnt "
    "CUE-D (\"Welche Elemente sind im Ausführungsprojekt?\" → "
    "out_of_scope, nicht aggregation).\n\n"
    "Typdefinitionen:\n"
    "• `point` — einzelne Faktenfrage (Wer/Wie heisst/Was ist X?, "
    "  einzelne Zahl, einzelnes Datum).\n"
    "• `aggregation` — Liste/Mehrzahl von Entitäten (Welche/Alle Y, "
    "  wer ist beteiligt, welche Drittprojekte tangieren).\n"
    "• `total` — Summe/Headline-Wert (Bausumme, Gesamtkosten, "
    "  Gesamtaufwand, wie viele Stunden insgesamt, Gesamttermin).\n"
    "• `phrase` — verbatim-Phrase- oder Zitatsuche (Steht der Satz "
    "  X in den Plänen?, Wird Y wörtlich erwähnt?).\n"
    "• `out_of_scope` — die Frage betrifft erkennbar SIA-Phasen "
    "  ausserhalb des Auftrags (z.B. SIA 32+ Bauprojekt, SIA 51+ "
    "  Ausführung) während die Beschaffung nur SIA 21/31 umfasst.\n\n"
    "SCHRITT 2 — RUBRIK PER FRAGENTYP:\n\n"
    "point: sufficient=true wenn der gesuchte Fakt in mindestens "
    "  einem Chunk explizit benannt ist. NICHT sufficient: 'Name "
    "  nicht enthalten', 'Formular ohne Daten', 'Rolle erwähnt aber "
    "  Person fehlt'.\n\n"
    "aggregation: sufficient=true nur wenn entweder (a) ≥3 distinkte "
    "  Entitäten der gefragten Kategorie in den Chunks belegt sind, "
    "  ODER (b) ein Chunk explizit aussagt, dass es nur X Einträge "
    "  gibt UND alle X in den Chunks enthalten sind. ALLEINSTEHEND "
    "  1-2 Entitäten bei einer 'welche'-Frage = insufficient. "
    "  Achtung: wenn alle Chunks Sub-Zeilen derselben Tabelle ohne "
    "  Headline-Zeile sind, ist das auch insufficient — dann fehlt "
    "  ggf. die abschliessende Aufzählung in einer anderen Section.\n\n"
    "total: sufficient=true nur wenn der HEADLINE-Wert (Gesamtsumme, "
    "  Total, Gesamtaufwand) explizit in einem Chunk steht. Wenn nur "
    "  Teilbeträge / Sub-Etappen / Tabellen-Sub-Zeilen vorhanden "
    "  sind aber kein Total-Wert, dann insufficient — auch wenn die "
    "  Teilbeträge sich rechnerisch aufsummieren liessen. Du sollst "
    "  Werte nicht selbst summieren.\n\n"
    "total — KONKRETE BEISPIEL-FÄLLE:\n\n"
    "Fall 1 (insufficient): Frage = \"Was ist die Bausumme?\", Chunks "
    "  zeigen Etappen-Werte (Etappe 1: CHF 1.9 Mio, Etappe 2: CHF 16.4 "
    "  Mio, …) ohne Total-Zeile. Auch wenn sich die Etappen-Werte zu "
    "  einem Total summieren liessen, ist das insufficient — die "
    "  Headline-Zeile mit dem Gesamtwert ist nicht belegt. Antwort: "
    "  sufficient=false, missing=\"Headline-Total-Zeile aus Tabelle 2 "
    "  (Gesamt-Bausumme als Summe über alle Etappen)\", feedback="
    "  \"`read_section(file_id=<X>, page_from=17, page_to=17, "
    "  include_page_neighbors=true)` um die Tabellen-Headline-Zeile "
    "  zu erfassen\".\n\n"
    "Fall 2 (insufficient): Frage = \"Wie viele Stunden insgesamt?\", "
    "  Chunks zeigen einzelne Phasenstunden (Federführung: 600 h) ohne "
    "  Gesamttotal. Insufficient — fehlt die Total-Zeile.\n\n"
    "Fall 3 (sufficient): Frage = \"Was ist die Bausumme?\", ein Chunk "
    "  enthält den Satz \"Die Grobkostenschätzung der Baukosten für "
    "  Etappen 1 bis 5 beträgt CHF 39'114'000 (exkl. MwSt.)\". "
    "  Sufficient — Headline-Wert ist explizit benannt.\n\n"
    "Fall 4 (insufficient — kritisch): wenn die Chunks ALLE aus "
    "  derselben Tabelle stammen (gleiches block_type=`table`, gleiches "
    "  heading_path) aber keine Headline-Zeile zeigen, ist das KEIN "
    "  Beleg für Vollständigkeit — die Headline-Zeile liegt typischer-"
    "  weise im selben Tabellenbereich auf derselben Seite und sollte "
    "  via `include_page_neighbors=true` zugänglich sein. Dies ist ein "
    "  STRUKTURELLER Hinweis, dass die Headline-Zeile auf derselben "
    "  Seite, aber in einem anderen Chunk lebt.\n\n"
    "phrase: sufficient=true nur wenn ein Chunk die genaue Phrase "
    "  oder eine semantisch identische Aussage enthält. Bei einer "
    "  Negativ-Antwort ('Phrase nicht gefunden') verlange "
    "  insufficient — der Agent soll dann via list_document_outline "
    "  noch eine andere Datei explorieren statt sofort 'nichts "
    "  gefunden' zu antworten.\n\n"
    "out_of_scope: sufficient=true wenn die Frage erkennbar SIA "
    "  32+ / Ausführung / Bauprojekt-Phase betrifft und der Auftrag "
    "  laut Chunks nur SIA 21/31 umfasst. Scope-Fallback ist KEINE "
    "  Insuffizienz — der Agent darf mit dem entsprechenden Hinweis "
    "  antworten.\n\n"
    "BEWERTUNGSHALTUNG: bewerte nüchtern. Eine `false`-Bewertung "
    "kostet maximal eine zusätzliche Retrieval-Runde; eine "
    "fälschliche `true`-Bewertung führt zu einer unvollständigen "
    "Antwort an den Nutzer.\n\n"
    "Bei `sufficient=false` MUSST du in `missing` konkret "
    "beschreiben WAS fehlt (1 Satz), und in `feedback` einen "
    "konkreten Vorschlag geben, welche Tools/Section/Datei der "
    "Agent als nächstes abfragen sollte (z.B. "
    "`list_document_outline` auf `<file_id-prefix>`, dann "
    "`read_section(section='...')`).\n\n"
    "ANTWORTE AUSSCHLIESSLICH MIT JSON IN DIESEM FORMAT:\n"
    '{"question_type": "point|aggregation|total|phrase|out_of_scope", '
    '"sufficient": true|false, "missing": "..." oder null, '
    '"feedback": "..." oder null}'
)


_RATER_EXCERPT_CHARS = 600  # plan 17.3 T5: bumped from 200 — fact-bearing
# chunks (figure captions naming a Projektleiter, table rows with totals) were
# being truncated before the entity appeared. 600 chars × 30 chunks ≈ 18k
# chars / ~6k tokens — still well under Gemini Flash's context budget.
_RATER_MAX_CHUNKS = 30


def _format_chunks_for_rater(chunks: list[RetrievedChunk]) -> str:
    """Render chunks compactly with metadata: filename, file_id-prefix,
    page, block_type, heading_path (first 4 levels), figure_label, and the
    first ~600 chars of content. Plan 17.4 T2c: heading_path + block_type
    let the rater spot 'all chunks are sub-rows of the same table; no
    headline row in the set' patterns."""
    if not chunks:
        return "(Keine Chunks retrieved.)"
    lines: list[str] = []
    for i, c in enumerate(chunks[:_RATER_MAX_CHUNKS], 1):
        excerpt = c.content[:_RATER_EXCERPT_CHARS]
        if len(c.content) > _RATER_EXCERPT_CHARS:
            excerpt += "…"
        prefix = c.file_id.replace("-", "")[:8] if c.file_id else "?"
        heading = " > ".join(c.heading_path[:4]) if c.heading_path else "-"
        figure = c.figure_label or "-"
        head = (
            f"[{i}] {c.filename}\n"
            f"    file_id: {prefix}\n"
            f"    page: {c.page_start}"
            + (f"-{c.page_end}" if c.page_end != c.page_start else "")
            + f"\n"
            f"    block_type: {c.block_type}\n"
            f"    heading_path: {heading}\n"
            f"    figure_label: {figure}\n"
            f"    excerpt:"
        )
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
        return {"sufficient": True, "missing": None, "feedback": None, "question_type": None}

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
        return {"sufficient": True, "missing": None, "feedback": None, "question_type": None}

    raw = (resp.choices[0].message.content or "").strip()
    if not raw:
        return {"sufficient": True, "missing": None, "feedback": None, "question_type": None}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("sufficiency_check: non-JSON response: %r", raw[:200])
        return {"sufficient": True, "missing": None, "feedback": None, "question_type": None}

    sufficient = bool(parsed.get("sufficient", True))
    missing = parsed.get("missing") if not sufficient else None
    feedback = parsed.get("feedback") if not sufficient else None
    # Plan 17.4 T1: log question_type for LangSmith trace audit. Not part of
    # the returned schema — callers don't branch on it; we just want to know
    # whether the rater's classification matches our expectations on the UAT
    # set so we can iterate on the rubric.
    qtype = parsed.get("question_type")
    log.info(
        "sufficiency_check verdict: type=%s sufficient=%s missing=%r",
        qtype if isinstance(qtype, str) else "?",
        sufficient,
        (missing if isinstance(missing, str) else "")[:120],
    )
    return {
        "sufficient": sufficient,
        "missing": missing if isinstance(missing, str) else None,
        "feedback": feedback if isinstance(feedback, str) else None,
        "question_type": qtype if isinstance(qtype, str) else None,
    }


def build_continuation_hint(
    verdict: SufficiencyVerdict,
    *,
    force_outline_file_id: str | None = None,
) -> str:
    """Format an insufficient verdict as a system message that nudges the
    agent into one more retrieval round. Caller should append this to
    `messages` between the assistant's would-be-final iteration and the
    next loop iteration.

    Plan 17.4 T3: when `force_outline_file_id` is set, swap the generic
    "make some retrieval call" suffix for a directive that pins the next
    call to `list_document_outline` on that file_id prefix. The chat loop
    pairs this with `tool_choice={"type":"function","function":{"name":
    "list_document_outline"}}` so the contract is enforced both via prose
    and via the OpenAI-compat tool_choice constraint.
    """
    parts = [
        "SUFFICIENCY-CHECK: deine bisherige Antwort deckt die Frage "
        "möglicherweise nicht vollständig ab."
    ]
    if verdict.get("missing"):
        parts.append(f"Fehlt: {verdict['missing']}")
    if verdict.get("feedback"):
        parts.append(f"Vorschlag: {verdict['feedback']}")
    if force_outline_file_id:
        parts.append(
            "Rufe als nächstes `list_document_outline` auf einer "
            f"wahrscheinlichen Datei auf (z.B. file_id="
            f"'{force_outline_file_id}'). Lies den Outline, "
            "identifiziere die richtige Sektion, und rufe danach "
            "`read_section` mit dem gefundenen Sektions-Namen auf. "
            "KEIN weiterer search_chunks-Aufruf — der hat den "
            "richtigen Chunk nicht gerankt."
        )
    else:
        parts.append(
            "Mache einen weiteren gezielten Retrieval-Tool-Aufruf "
            "(`search_chunks` mit anderem Suchbegriff, "
            "`list_document_outline` auf einer wahrscheinlichen Datei, "
            "oder `read_section` auf der vermuteten Stelle) und "
            "antworte dann."
        )
    return " ".join(parts)
