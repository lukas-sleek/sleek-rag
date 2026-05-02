"""Probe 2 — does subclassing `VertexAiRagRetrieval.run_async` actually fire?

Source read in T0 reveals: VertexAiRagRetrieval.process_llm_request, for any
Gemini 2.x model, registers the tool as a *server-side* `Tool(retrieval=...)`
on the LlmRequest.config.tools — bypassing the function-declaration path.
That means run_async is NEVER called for gemini-2.5-flash/pro.

Two recovery options exist:
  (A) Set ADK_DISABLE_GEMINI_MODEL_ID_CHECK=1 globally → this also bypasses
      the Gemini-2 detection in process_llm_request. The tool then registers
      a function declaration and run_async fires.
  (B) Don't subclass at all — write a fresh FunctionTool wrapping
      rag.async_retrieve_contexts.

This probe verifies option (A): with the env var set, does our subclass's
run_async actually run, and does state["citations"] survive the turn?
"""
from __future__ import annotations

import asyncio
import json
import os

# Force the function-declaration path BEFORE importing ADK retrieval module.
os.environ["ADK_DISABLE_GEMINI_MODEL_ID_CHECK"] = "1"

from _common import CORPUS_NAME, USER_ID  # noqa: F401, side effect

from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools import ToolContext
from google.adk.tools.retrieval import VertexAiRagRetrieval
from vertexai.preview import rag
from vertexai.preview.reasoning_engines import AdkApp


class CitationPreservingRagRetrieval(VertexAiRagRetrieval):
    async def run_async(self, *, args, tool_context: ToolContext):
        print(f"[probe2] run_async fired with args={args!r}")
        response = await rag.async_retrieve_contexts(
            text=args["query"],
            rag_resources=self.vertex_rag_store.rag_resources,
            rag_corpora=self.vertex_rag_store.rag_corpora,
            rag_retrieval_config=rag.RagRetrievalConfig(
                top_k=self.vertex_rag_store.similarity_top_k or 5,
            ),
        )
        contexts = response.contexts.contexts
        print(f"[probe2] async_retrieve_contexts returned {len(contexts)} contexts")
        if not contexts:
            return {"status": "no_results", "chunks": []}

        cits = tool_context.state.setdefault("citations", [])
        chunks = []
        for i, ctx in enumerate(contexts, start=len(cits) + 1):
            cits.append(
                {
                    "idx": i,
                    "uri": getattr(ctx, "source_uri", None),
                    "filename": getattr(ctx, "source_display_name", None),
                    "score": getattr(ctx, "score", None),
                    "snippet": (ctx.text or "")[:120],
                }
            )
            chunks.append(
                {
                    "idx": i,
                    "filename": getattr(ctx, "source_display_name", None),
                    "text": ctx.text or "",
                }
            )
        return {"status": "ok", "chunks": chunks}


async def main():
    tool = CitationPreservingRagRetrieval(
        name="search_project_documents",
        description=(
            "Hybrid search the project's RAG corpus. Returns chunks with "
            "filename and page metadata."
        ),
        rag_resources=[rag.RagResource(rag_corpus=CORPUS_NAME)],
        similarity_top_k=5,
    )
    agent = LlmAgent(
        name="probe2_agent",
        model="gemini-2.5-flash",
        description="Probe 2 agent",
        instruction=(
            "Du nutzt search_project_documents um Informationen aus den "
            "Projektdokumenten abzurufen. Antworte auf Deutsch."
        ),
        tools=[tool],
    )
    app = AdkApp(agent=agent)

    n = 0
    final_session_id = None
    async for ev in app.async_stream_query(
        message="Was ist das Bauvorhaben in den Dokumenten?",
        user_id=USER_ID,
    ):
        n += 1
        if n <= 8:
            print(f"--- event {n} keys: {list(ev.keys()) if isinstance(ev, dict) else type(ev).__name__}")
        if isinstance(ev, dict):
            final_session_id = ev.get("session_id") or final_session_id
            actions = ev.get("actions") or {}
            sd = actions.get("state_delta") or {}
            if "citations" in sd:
                print(f"[probe2] state_delta.citations seen: {len(sd['citations'])} entries")

    print(f"\n[probe2] total events: {n}")
    print(f"[probe2] final session_id: {final_session_id}")
    if final_session_id:
        sess = await app.async_get_session(user_id=USER_ID, session_id=final_session_id)
        # AdkApp sessions are dicts at this layer.
        state = sess.get("state") if isinstance(sess, dict) else getattr(sess, "state", None)
        cits = (state or {}).get("citations") if state else None
        print(f"[probe2] session.state['citations']: {json.dumps(cits, indent=2, default=str)[:1500]}")


if __name__ == "__main__":
    asyncio.run(main())
