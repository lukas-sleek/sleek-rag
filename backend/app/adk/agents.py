"""ADK agent factories + module-level constants.

Tree shape:

    chat_orchestrator (gemini-2.5-flash)
      tool: rag_specialist (StreamingAgentTool)
        tool: VertexAiRagRetrieval — Gemini-native managed retrieval
              (injected as `Tool(retrieval=...)` for Gemini 2+, lets the
               model think → retrieve → think → retrieve in one inference)
      tool: web_researcher (StreamingAgentTool)
        tool: web_google_search (StreamingAgentTool)
        tool: web_url_fetcher (StreamingAgentTool)
      tool: run_projektanalyse (FunctionTool, fan-out der User-Vorlage)

Why managed retrieval: the previous custom `search_project_documents`
FunctionTool forced exactly one retrieval per ADK round-trip — the model
got two thinking passes (planning + post-retrieval analysis) regardless
of question complexity. The native `Tool(retrieval=Retrieval(vertex_rag_
store=...))` runs server-side during model inference, so Gemini can chain
think → retrieve → refine → retrieve → answer in a single response,
matching the rich iterative reasoning Vertex Agent Builder demonstrates
with the same model + thinking_config.
"""
from __future__ import annotations

from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools import url_context
from google.adk.tools.google_search_tool import GoogleSearchTool
from google.adk.tools.retrieval.vertex_ai_rag_retrieval import VertexAiRagRetrieval
from google.genai import types as genai_types
from vertexai.preview import rag

from .dispatch_rag_questions_tool import make_dispatch_rag_questions_tool
from .instructions import CHAT_ORCHESTRATOR_INSTRUCTION, RAG_SPECIALIST_INSTRUCTION
from .projektanalyse_tool import make_run_projektanalyse_tool
from .streaming_agent_tool import StreamingAgentTool


# ---------------------------------------------------------------------------
# Shared retry config — applied to every LlmAgent in the tree.
#
# DSQ shared-pool throttles surface as bare 429 RESOURCE_EXHAUSTED with no
# QuotaFailure detail (see backend/scripts/dsq_diagnose.py for the proof).
# Without retry the whole chat turn fails; with backoff, the second/third
# try clears because DSQ pool capacity recovers in seconds.
#
# attempts=4 + exp_base=2 + initial_delay=1 -> waits ~1s, 2s, 4s, then gives up
# (capped by max_delay=20). Burst-friendly without piling up if a real
# outage occurs.
# ---------------------------------------------------------------------------

_HTTP_OPTIONS = genai_types.HttpOptions(
    retry_options=genai_types.HttpRetryOptions(
        attempts=4,
        initial_delay=1.0,
        max_delay=20.0,
        exp_base=2.0,
        http_status_codes=[429, 500, 502, 503, 504],
    )
)

_RETRY_CONFIG = genai_types.GenerateContentConfig(http_options=_HTTP_OPTIONS)


# Sub-agent thinking config: keep unbounded thinking budget (model decides
# how long to deliberate before producing output — single-question deep
# reasoning + retrieval genuinely benefits, per-call latency stays in the
# 10-25s range), but DON'T emit the chain-of-thought to the stream. The
# activity panel previously rendered these as "denkt laut" rows; removed
# 2026-05 because the surface added clutter without enough debugging value
# to justify the bandwidth and rendering cost.
_THINKING_CONFIG = genai_types.ThinkingConfig(
    include_thoughts=False,
    thinking_budget=-1,
)

# Orchestrator: no thinking at all. Matches the pre-`01a1437` (`24c2534`)
# branch behavior where the orchestrator ran on plain `_RETRY_CONFIG` and
# 11-question summaries returned in seconds, not minutes. The orchestrator's
# instruction is prescriptive ("pass through unchanged") so it doesn't
# benefit from chain-of-thought; the latency cost was pure loss. Sub-agent
# (`rag_specialist`) thinking stays on — that's where it legitimately helps.
_ORCHESTRATOR_THINKING_CONFIG = genai_types.ThinkingConfig(
    include_thoughts=False,
    thinking_budget=0,
)


def _retry_with_thinking() -> genai_types.GenerateContentConfig:
    """Per-agent generate-content config: retry + unbounded thinking budget
    with no thought-text emission."""
    return genai_types.GenerateContentConfig(
        http_options=_HTTP_OPTIONS,
        thinking_config=_THINKING_CONFIG,
    )


def _retry_with_orchestrator_thinking() -> genai_types.GenerateContentConfig:
    """Orchestrator-only config: thinking disabled entirely so post-tool
    aggregation doesn't burn minutes. See _THINKING_CONFIG block above."""
    return genai_types.GenerateContentConfig(
        http_options=_HTTP_OPTIONS,
        thinking_config=_ORCHESTRATOR_THINKING_CONFIG,
    )


# ---------------------------------------------------------------------------
# Per-project (corpus-bound) sub-tree
# ---------------------------------------------------------------------------


_RETRIEVAL_TOP_K = 10


def make_rag_specialist(corpus_name: str) -> LlmAgent:
    """Per-question RAG worker. Uses Gemini's NATIVE managed retrieval.

    `VertexAiRagRetrieval` injects a `Tool(retrieval=Retrieval(vertex_rag_
    store=...))` directly into the GenerateContent config (see ADK's
    `google/adk/tools/retrieval/vertex_ai_rag_retrieval.py:67-81`). For
    Gemini 2+ models the retrieval runs server-side during inference, so
    the model can chain multiple think → retrieve → think iterations in a
    single response — same pattern as Vertex Agent Builder.

    Citations come back via `event.grounding_metadata.grounding_chunks`
    (each carrying `retrieved_context.{text, title, uri}`); `chats.py`
    extracts these from the StreamingAgentTool's propagated grounding
    metadata and turns them into the [N] citation records the chat UI
    already knows how to render.
    """
    retrieval_tool = VertexAiRagRetrieval(
        name="search_project_documents",
        description=(
            "Searches the project's RAG corpus (Vertex AI Search-managed "
            "store) for chunks relevant to a query. Returns excerpts the "
            "model can use to ground its answer. Use whenever the question "
            "is about content of the uploaded project documents."
        ),
        rag_resources=[rag.RagResource(rag_corpus=corpus_name)],
        similarity_top_k=_RETRIEVAL_TOP_K,
    )
    return LlmAgent(
        name="rag_specialist",
        model="gemini-2.5-flash",
        description=(
            "Beantwortet GENAU EINE Sachfrage zu den Projektdokumenten "
            "(Schweizer Bahn-/Ingenieurprojekt-Ausschreibungen) ausschliesslich "
            "anhand des Projekt-Korpus. Liefert eine knappe, faktenbasierte "
            "Antwort mit Inline-Zitationen [1], [2], … und einer Quellenliste. "
            "Erfindet keine Werte und summiert keine Teilbetraege selbst. "
            "Vom Chat-Agenten pro Einzelfrage delegiert."
        ),
        instruction=RAG_SPECIALIST_INSTRUCTION,
        tools=[retrieval_tool],
        generate_content_config=_retry_with_thinking(),
    )


# ---------------------------------------------------------------------------
# Corpus-independent web sub-tree (module-level constants — no per-project
# state, safe to share across cached AdkApps).
# ---------------------------------------------------------------------------


web_google_search = LlmAgent(
    name="web_google_search",
    model="gemini-2.5-flash",
    description=(
        "Findet oeffentlich verfuegbare Quellen im Web zu einer konkreten "
        "Suchanfrage via Google. Gibt Titel, URL und Snippet pro Treffer "
        "zurueck. Wird ausschliesslich vom web_researcher aufgerufen."
    ),
    instruction=(
        "Rufe GoogleSearchTool mit der vom web_researcher uebergebenen "
        "Anfrage auf. Gib die Trefferliste unveraendert zurueck — keine "
        "Bewertung, keine Auswahl."
    ),
    tools=[GoogleSearchTool()],
    generate_content_config=_RETRY_CONFIG,
)


web_url_fetcher = LlmAgent(
    name="web_url_fetcher",
    model="gemini-2.5-flash",
    description=(
        "Laedt den Inhalt einer oder mehrerer URLs und gibt den extrahierten "
        "Text zurueck. Wird vom web_researcher aufgerufen, nachdem "
        "web_google_search relevante URLs geliefert hat."
    ),
    instruction=(
        "Verwende UrlContext, um die uebergebenen URLs abzurufen. Gib den "
        "Inhalt pro URL klar getrennt zurueck — keine Zusammenfassung."
    ),
    tools=[url_context],
    generate_content_config=_RETRY_CONFIG,
)


web_researcher = LlmAgent(
    name="web_researcher",
    model="gemini-2.5-flash",
    description=(
        "Beantwortet EINE Frage anhand oeffentlicher Web-Quellen. Wird vom "
        "Chat-Agenten nur dann aufgerufen, wenn die Frage explizit nach "
        "externen Informationen verlangt (Marktpreise, Normen, Firmen-"
        "Hintergruende, Standards) und NICHT durch die Projektdokumente "
        "beantwortet werden kann. Liefert eine Antwort mit URL-Zitationen "
        "[1], [2], …"
    ),
    instruction=(
        "Sprache: HOCHDEUTSCH (Standard-Deutsch), KEIN Schweizerdeutsch / "
        "Mundart. Verwende NICHT 'isch', 'het', 'gfunde', 'bsunders' etc. "
        "ASCII-Spelling: ae/oe/ue statt Umlauten, ss statt ss-Ligatur — "
        "das ist nur eine Zeichensatz-Regel; Wortwahl bleibt Hochdeutsch.\n\n"
        "Vorgehen:\n"
        "1. Formuliere eine praezise Suchanfrage, rufe web_google_search auf.\n"
        "2. Waehle die 1-3 relevantesten Treffer und rufe web_url_fetcher "
        "fuer deren URLs auf.\n"
        "3. Antworte faktenbasiert in Hochdeutsch mit Inline-Zitationen [N], "
        "gefolgt von einer Quellenliste.\n"
        "4. Wenn die Web-Recherche keine belastbare Antwort liefert, sage "
        "'im Web nicht belegt'.\n\n"
        "QUELLEN-FORMAT (PFLICHT, MASCHINELL GEPARSED):\n"
        "Beende JEDE Antwort mit einem Block, der EXAKT so beginnt:\n"
        "    Quellen:\n"
        "Pro zitiertem [N] eine Zeile in EXAKT diesem Schema (nichts davor, "
        "nichts dazwischen, eine Zeile pro Quelle):\n"
        "    [N] <https-url> — <kurzer Titel>\n"
        "Beispiel:\n"
        "    Quellen:\n"
        "    [1] https://www.example.com/cv-pascal-ryser — Noser Engineering: Pascal Ryser\n"
        "    [2] https://en.wikipedia.org/wiki/Foo_Bar — Wikipedia: Foo Bar\n"
        "Halte das Format auch dann ein, wenn nur eine Quelle vorhanden ist. "
        "Wenn keine Quelle belastbar ist, lasse den Block weg und schreibe "
        "stattdessen 'im Web nicht belegt'."
    ),
    tools=[
        StreamingAgentTool(agent=web_google_search),
        StreamingAgentTool(agent=web_url_fetcher),
    ],
    generate_content_config=_RETRY_CONFIG,
)


# ---------------------------------------------------------------------------
# Top-level orchestrator factory
# ---------------------------------------------------------------------------


def make_chat_orchestrator(corpus_name: str) -> LlmAgent:
    """Top-level chat agent. Builds a fresh rag_specialist (corpus-bound)
    and wires it alongside the corpus-independent web_researcher, the
    dispatch_rag_questions fan-out tool, and the run_projektanalyse
    template-fan-out tool.

    rag_specialist is constructed ONCE per orchestrator and shared by both
    the single-question StreamingAgentTool path and the multi-question
    dispatch_rag_questions path. This keeps Vertex RAG retrieval bound to
    a single LlmAgent instance regardless of how it's invoked.
    """
    rag_specialist = make_rag_specialist(corpus_name)
    return LlmAgent(
        name="chat_orchestrator",
        # Flash for orchestrator latency. The original plan called for Pro
        # because of routing/rephrasing nuance, but live testing showed
        # Flash with a tightened instruction (explicit counting rule + worked
        # multi-question examples) handles N-way fan-out reliably and ships
        # 2-3x faster.
        model="gemini-2.5-flash",
        description=(
            "Hauptagent im Dialog mit dem Nutzer. Versteht die Nutzeranfrage, "
            "entscheidet ueber das Routing (rag_specialist fuer eine einzelne "
            "Projektfrage, dispatch_rag_questions fuer 2+ unabhaengige "
            "Projektfragen, web_researcher fuer externe Recherche, "
            "run_projektanalyse fuer die hinterlegte Vorlage, Direktantwort "
            "bei Smalltalk und reinen Folgefragen) und fasst Sub-Agent-"
            "Antworten zu einer kohaerenten Antwort zusammen."
        ),
        instruction=CHAT_ORCHESTRATOR_INSTRUCTION,
        tools=[
            # propagate_grounding_metadata=True forwards rag_specialist's
            # GroundingMetadata (sources + supports + segments from the
            # native vertex_rag_store retrieval) into the orchestrator's
            # tool_context.state under "temp:_adk_grounding_metadata", so
            # chats.py can build citation records from it.
            StreamingAgentTool(
                agent=rag_specialist,
                propagate_grounding_metadata=True,
            ),
            # Deterministic N-way fan-out for multi-question turns. Replaces
            # relying on Flash to emit N parallel function calls (which it
            # does unreliably). See dispatch_rag_questions_tool.py for the
            # empirical justification.
            make_dispatch_rag_questions_tool(rag_specialist),
            make_run_projektanalyse_tool(rag_specialist),
            StreamingAgentTool(agent=web_researcher),
        ],
        generate_content_config=_retry_with_orchestrator_thinking(),
    )
