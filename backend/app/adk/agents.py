"""ADK agent factories + module-level constants (plan 19.0 T3-T7).

Tree shape (post-collapse, see comment below):

    chat_orchestrator (gemini-2.5-flash)
      tool: rag_specialist (AgentTool)
        tool: search_project_documents (FunctionTool, corpus-bound)
      tool: web_researcher (AgentTool)
        tool: web_google_search (AgentTool)
        tool: web_url_fetcher (AgentTool)
      tool: run_projektanalyse_v2 (FunctionTool)

Per-corpus state lives in the closure of make_search_project_documents_tool;
each cached AdkApp owns its own orchestrator subtree.

History note: an intermediate `document_retriever` LlmAgent layer used to
sit between rag_specialist and search_project_documents. Its instruction
was a 100% verbatim passthrough ("call the tool with the query and return
the chunks unchanged") — pure indirection inherited from an earlier
plan that envisioned a managed VertexAiRagRetrieval tool. We collapsed it
to cut the agent tree's per-sub-question Flash call count from 6 to 4
(-33%), which directly reduces DSQ shared-pool burst pressure during
N-question chat turns.
"""
from __future__ import annotations

from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools import agent_tool, url_context
from google.adk.tools.google_search_tool import GoogleSearchTool
from google.genai import types as genai_types

from app.projektanalyse_v2_tool import run_projektanalyse_v2_tool

from .instructions import CHAT_ORCHESTRATOR_INSTRUCTION, RAG_SPECIALIST_INSTRUCTION
from .retrieval_tool import make_search_project_documents_tool


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


def make_rag_specialist(corpus_name: str) -> LlmAgent:
    """Per-question RAG worker. Owns SIA domain rules + [N] citation contract.

    Holds the corpus-bound search_project_documents FunctionTool directly
    (no intermediate document_retriever LlmAgent — see module docstring).
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
        tools=[make_search_project_documents_tool(corpus_name)],
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
