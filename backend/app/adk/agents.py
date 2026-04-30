"""ADK agent factories + module-level constants.

Tree shape:

    chat_orchestrator (gemini-2.5-flash)
      tool: rag_specialist (AgentTool)
        tool: document_retriever (AgentTool)
          tool: VertexAiRagRetrieval (managed — server-side Tool(retrieval=...))
      tool: web_researcher (AgentTool)
        tool: web_google_search (AgentTool)
        tool: web_url_fetcher (AgentTool)
      tool: run_projektanalyse_v2 (FunctionTool)

The document_retriever's `after_model_callback` translates Gemini's
`grounding_metadata` into the citation records `state["citations"]` that
the rest of the pipeline already consumes. See `retrieval_tool.py`.
"""
from __future__ import annotations

from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools import agent_tool, url_context
from google.adk.tools.google_search_tool import GoogleSearchTool
from google.genai import types as genai_types

from app.projektanalyse_v2_tool import run_projektanalyse_v2_tool

from .instructions import CHAT_ORCHESTRATOR_INSTRUCTION, RAG_SPECIALIST_INSTRUCTION
from .retrieval_tool import capture_grounding_callback, make_rag_tool


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

_RETRY_CONFIG = genai_types.GenerateContentConfig(
    http_options=genai_types.HttpOptions(
        retry_options=genai_types.HttpRetryOptions(
            attempts=4,
            initial_delay=1.0,
            max_delay=20.0,
            exp_base=2.0,
            http_status_codes=[429, 500, 502, 503, 504],
        )
    )
)


# ---------------------------------------------------------------------------
# Per-project (corpus-bound) sub-tree
# ---------------------------------------------------------------------------


def make_document_retriever(corpus_name: str) -> LlmAgent:
    """Per-project document retriever using server-side managed retrieval.

    The `VertexAiRagRetrieval` tool registers a server-side
    `Tool(retrieval=Retrieval(vertex_rag_store=...))` on this agent's LLM
    request — the model itself rewrites the query, retrieves chunks, and
    grounds its answer. `capture_grounding_callback` then translates the
    response's `grounding_metadata` into the citation records the rest of
    the pipeline (rag_specialist, citation_aggregator) already expects.
    """
    return LlmAgent(
        name="document_retriever",
        model="gemini-2.5-flash",
        description=(
            "Ruft relevante Textstellen aus dem RAG-Korpus des aktuellen "
            "Projekts ab und liefert eine fundierte Antwort mit "
            "Inline-Zitationen [N]. Wird ausschliesslich vom "
            "rag_specialist als Werkzeug aufgerufen, nie direkt vom "
            "Chat-Agenten."
        ),
        instruction=(
            "Beantworte die vom rag_specialist uebergebene Suchanfrage "
            "ausschliesslich mit Inhalten aus dem Projektkorpus. Nutze "
            "das verfuegbare Retrieval-Tool, um relevante Stellen zu "
            "finden, und antworte knapp + faktentreu in Hochdeutsch. "
            "Erfinde keine Werte. Wenn der Korpus keine belastbare "
            "Antwort liefert, sage 'Keine Treffer'. Inline-Zitationen "
            "[N] werden vom System aus den Grounding-Daten ergaenzt — "
            "schreibe sie nicht selbst, formuliere die Antwort so, dass "
            "die Zitationen am Satzende sinnvoll platziert werden."
        ),
        tools=[make_rag_tool(corpus_name)],
        after_model_callback=capture_grounding_callback,
        generate_content_config=_RETRY_CONFIG,
    )


def make_rag_specialist(corpus_name: str) -> LlmAgent:
    """Per-question RAG worker. Owns SIA domain rules + [N] citation contract.

    [TEMPORARILY routes through document_retriever again — A/B test, see
    make_document_retriever above.]
    """
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
        tools=[agent_tool.AgentTool(agent=make_document_retriever(corpus_name))],
        generate_content_config=_RETRY_CONFIG,
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
        agent_tool.AgentTool(agent=web_google_search),
        agent_tool.AgentTool(agent=web_url_fetcher),
    ],
    generate_content_config=_RETRY_CONFIG,
)


# ---------------------------------------------------------------------------
# Top-level orchestrator factory
# ---------------------------------------------------------------------------


def make_chat_orchestrator(corpus_name: str) -> LlmAgent:
    """Top-level chat agent. Builds a fresh rag_specialist (corpus-bound)
    and wires it alongside the corpus-independent web_researcher and the
    run_projektanalyse_v2 hand-off tool.
    """
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
            "entscheidet ueber das Routing (rag_specialist fuer Projekt-"
            "fragen, web_researcher fuer externe Recherche, "
            "run_projektanalyse_v2 nur auf explizite Anfrage, Direktantwort "
            "bei Smalltalk und reinen Folgefragen) und fasst Sub-Agent-"
            "Antworten zu einer kohaerenten Antwort zusammen."
        ),
        instruction=CHAT_ORCHESTRATOR_INSTRUCTION,
        tools=[
            agent_tool.AgentTool(agent=make_rag_specialist(corpus_name)),
            agent_tool.AgentTool(agent=web_researcher),
            run_projektanalyse_v2_tool,
        ],
        generate_content_config=_RETRY_CONFIG,
    )
