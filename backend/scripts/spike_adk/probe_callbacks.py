"""Probe 3 — does `after_tool_callback` fire for our subclassed retrieval?

Issue #2629 says before_tool_callback doesn't fire for VertexAiRagRetrieval.
We need to know if after_tool_callback also doesn't, so we can confirm the
"capture citations inside run_async" design is the only path (or whether
we have a dual-channel option).

Runs the same setup as probe 2 but registers an after_tool_callback that
just prints a marker.
"""
from __future__ import annotations

import asyncio
import os

os.environ["ADK_DISABLE_GEMINI_MODEL_ID_CHECK"] = "1"

from _common import CORPUS_NAME, USER_ID  # noqa: F401

from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools.retrieval import VertexAiRagRetrieval
from vertexai.preview import rag
from vertexai.preview.reasoning_engines import AdkApp

callback_invocations: list[dict] = []


def after_tool(tool, args, tool_context, tool_response):
    callback_invocations.append(
        {
            "tool_name": getattr(tool, "name", "unknown"),
            "args_keys": list(args.keys()) if isinstance(args, dict) else None,
            "response_type": type(tool_response).__name__,
        }
    )
    print(f"[probe3] after_tool_callback fired: tool={getattr(tool, 'name', '?')}")


async def main():
    tool = VertexAiRagRetrieval(
        name="search_project_documents",
        description="Search the project corpus.",
        rag_resources=[rag.RagResource(rag_corpus=CORPUS_NAME)],
        similarity_top_k=5,
    )
    agent = LlmAgent(
        name="probe3_agent",
        model="gemini-2.5-flash",
        description="Probe 3 agent",
        instruction="Verwende search_project_documents fuer Projektfragen.",
        tools=[tool],
        after_tool_callback=after_tool,
    )
    app = AdkApp(agent=agent)
    async for _ in app.async_stream_query(
        message="Was ist das Bauvorhaben?", user_id=USER_ID
    ):
        pass

    print(f"\n[probe3] callback fired {len(callback_invocations)} times")
    for inv in callback_invocations:
        print(f"  - {inv}")


if __name__ == "__main__":
    asyncio.run(main())
