"""ADK agent factories + module-level constants (plan 19.0 T3-T7).

Tree shape:

    chat_orchestrator (gemini-2.5-pro)
      tool: rag_specialist (AgentTool)
        tool: document_retriever (AgentTool)
          tool: search_project_documents (FunctionTool, corpus-bound)
      tool: web_researcher (AgentTool)
        tool: web_google_search (AgentTool)
        tool: web_url_fetcher (AgentTool)
      tool: run_projektanalyse_v2 (FunctionTool)

Per-corpus state lives in the closure of make_search_project_documents_tool;
each cached AdkApp owns its own orchestrator subtree.
"""
from __future__ import annotations

from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools import agent_tool, url_context
from google.adk.tools.google_search_tool import GoogleSearchTool

from app.projektanalyse_v2_tool import run_projektanalyse_v2_tool

from .instructions import CHAT_ORCHESTRATOR_INSTRUCTION, RAG_SPECIALIST_INSTRUCTION
from .retrieval_tool import make_search_project_documents_tool


# ---------------------------------------------------------------------------
# Per-project (corpus-bound) sub-tree
# ---------------------------------------------------------------------------


def make_document_retriever(corpus_name: str) -> LlmAgent:
    """Per-project document retriever. Bound to a specific RAG corpus.

    Constructed inside the per-corpus AdkApp factory; one instance per
    cached AdkApp.
    """
    return LlmAgent(
        name="document_retriever",
        model="gemini-2.5-flash",
        description=(
            "Ruft relevante Textstellen aus dem RAG-Korpus des aktuellen "
            "Projekts ab. Gibt rohe Chunks mit Quellangabe (Datei, Seite, "
            "Score) zurueck — ohne Interpretation. Wird ausschliesslich "
            "vom rag_specialist als Werkzeug aufgerufen, nie direkt vom "
            "Chat-Agenten."
        ),
        instruction=(
            "Du rufst das Tool search_project_documents mit der vom "
            "rag_specialist uebergebenen Suchanfrage auf. Gib die Treffer "
            "wortwoertlich und vollstaendig zurueck — keine Zusammen-"
            "fassung, keine Auswahl, keine Reformulierung. Wenn das Tool "
            "{'status': 'no_results'} meldet, gib das explizit als "
            "'Keine Treffer' zurueck."
        ),
        tools=[make_search_project_documents_tool(corpus_name)],
    )


def make_rag_specialist(corpus_name: str) -> LlmAgent:
    """Per-question RAG worker. Owns SIA domain rules + [N] citation contract."""
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
        "Vorgehen:\n"
        "1. Formuliere eine praezise Suchanfrage, rufe web_google_search auf.\n"
        "2. Waehle die 1-3 relevantesten Treffer und rufe web_url_fetcher "
        "fuer deren URLs auf.\n"
        "3. Antworte auf Schweizer Deutsch (ohne Umlaute/ss) faktenbasiert "
        "mit Inline-Zitationen [N], gefolgt von einer Quellenliste mit URLs.\n"
        "4. Wenn die Web-Recherche keine belastbare Antwort liefert, sage "
        "'im Web nicht belegt'."
    ),
    tools=[
        agent_tool.AgentTool(agent=web_google_search),
        agent_tool.AgentTool(agent=web_url_fetcher),
    ],
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
    )
