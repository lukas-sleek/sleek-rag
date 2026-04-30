"""Hand-off FunctionTool for run_projektanalyse_v2 (plan 19.0 T8a).

The orchestrator emits a function call with name "run_projektanalyse_v2";
the SSE translator in app/routers/chats.py sees the sentinel response and
aborts the orchestrator stream to resume from the existing v2 streamer.

The function name (`run_projektanalyse_v2`) is what ADK exposes to the
LLM's function-calling layer — the event translator's `is_v2_handoff`
matches on it verbatim.
"""
from google.adk.tools import FunctionTool, ToolContext


# No `from __future__ import annotations`: ADK's tool-declaration builder
# evaluates parameter annotations via `typing.get_type_hints`, which fails on
# stringised forwards refs that reference ADK's own ToolContext. Keeping the
# annotation as a real type makes registration work end-to-end.
async def run_projektanalyse_v2(tool_context: ToolContext) -> dict:
    """Volltext-Analyse: laedt das gesamte Projekt-Korpus in einen einzigen \
Gemini-Aufruf und beantwortet die Vorlage-Fragen darueber. Kein retrieval-\
basiertes Grounding — alles ist im Kontext. USE WHEN: Der Nutzer fordert \
explizit eine Volltext-/v2-Analyse, 'Projektanalyse v2', 'v2-Analyse' oder \
'vollstaendige Analyse mit allen Dokumenten'. DO NOT auto-escalate."""
    return {"hand_off": "projektanalyse_v2"}


run_projektanalyse_v2_tool = FunctionTool(func=run_projektanalyse_v2)
