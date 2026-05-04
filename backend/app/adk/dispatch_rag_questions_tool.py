"""Deterministic fan-out for batched RAG questions.

Empirically validated 2026-05-02 against the Suedi-Areal corpus
(see backend/scripts/test_batched_rag_recall.py):

  - Concatenating 11 questions into one Gemini call with native
    Tool(retrieval=...) MISSES facts that single-question calls retrieve
    cleanly (Bausumme 39'114'000 disappears).
  - Asking the SAME 11 questions as 11 parallel calls recovers the
    Bausumme reliably and recovers per-question recall.

The chat orchestrator instruction asks Gemini 2.5 Flash to emit N
parallel rag_specialist function calls when the user prompt has N
distinct questions. Flash empirically does not obey reliably — it
often fuses the prompt into ONE rag_specialist(request="<all 11>")
call which then re-triggers the batch failure mode. This module
replaces that flaky model behaviour with a single deterministic tool
the orchestrator can call: dispatch_rag_questions(questions=[...]).

Concurrency cap: a naive asyncio.gather across 11 parallel rag_specialist
runs took 608s in testing (DSQ pool saturated, ADK retries serialised
through 1s/2s/4s exponential backoff). Semaphore(4) keeps wallclock at
~3 batches × ~19s = ~60s.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
from typing import Any

from google.adk.agents.llm_agent import LlmAgent
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.tools import FunctionTool, ToolContext
from google.genai import types as genai_types

from ._harpoon_retry import (
    DEFAULT_MAX_ATTEMPTS,
    harpoon_backoff_delay,
    is_harpoon_transient,
)
from .streaming_agent_tool import StreamingAgentTool

log = logging.getLogger(__name__)

DISPATCH_CONCURRENCY = 4

# Per-request progress channel. The chat router sets this to an asyncio.Queue
# before invoking app.async_stream_query so that as each sub-rag_specialist
# call starts and finishes inside dispatch_rag_questions, we can push live
# progress events into the SSE stream alongside ADK's own events. Without
# this, dispatch_rag_questions is a black box: one tool_call goes out, one
# tool_response comes back, and the user stares at a spinner with no signal
# for ~30s+. Set to None when no listener is attached (production path that
# doesn't want progress, tests, etc.) — the put becomes a no-op.
DISPATCH_PROGRESS_CHAN: contextvars.ContextVar[asyncio.Queue | None] = (
    contextvars.ContextVar("dispatch_progress_chan", default=None)
)


async def _run_one_rag_specialist(
    agent: LlmAgent, question: str, *, user_id: str
) -> tuple[str, Any]:
    """Run rag_specialist once via ADK Runner. Returns (text, grounding_metadata).

    Each call gets its own Runner + session — parallel calls cannot share
    state. We accumulate the LAST event with content (mirrors what
    StreamingAgentTool.run_async does upstream).

    Wraps the iteration in Harpoon-retry: parallel sub-calls that hit the
    Vertex Managed Vector Search transient (URL_REJECTED Reason 54) silently
    retry up to DEFAULT_MAX_ATTEMPTS times before bubbling the failure to
    fanout_rag_specialist's per-question fallback. See _harpoon_retry.py.
    """
    content = genai_types.Content(
        role="user",
        parts=[genai_types.Part.from_text(text=question)],
    )

    async def _attempt() -> tuple[str, Any]:
        runner = Runner(
            app_name="dispatch_rag_questions",
            agent=agent,
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
        )
        session = await runner.session_service.create_session(
            app_name="dispatch_rag_questions",
            user_id=user_id,
        )
        last_text = ""
        last_gm = None
        try:
            async for event in runner.run_async(
                user_id=session.user_id,
                session_id=session.id,
                new_message=content,
            ):
                if event.content and event.content.parts:
                    text = "\n".join(
                        p.text for p in event.content.parts
                        if p.text and not getattr(p, "thought", False)
                    )
                    if text:
                        last_text = text
                        last_gm = (
                            getattr(event, "grounding_metadata", None) or last_gm
                        )
        finally:
            await runner.close()
        return (last_text.strip(), last_gm)

    label = f"rag_specialist[{question[:40]!r}]"
    last_exc: BaseException | None = None
    for attempt in range(DEFAULT_MAX_ATTEMPTS):
        try:
            return await _attempt()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not is_harpoon_transient(exc):
                raise
            if attempt + 1 >= DEFAULT_MAX_ATTEMPTS:
                log.warning(
                    "%s: harpoon retries exhausted (%d/%d). detail=%s",
                    label, attempt + 1, DEFAULT_MAX_ATTEMPTS,
                    str(exc).split("Events {")[0][:200],
                )
                raise
            delay = harpoon_backoff_delay(attempt)
            log.warning(
                "%s: harpoon transient; retry %d/%d in %.1fs.",
                label, attempt + 1, DEFAULT_MAX_ATTEMPTS, delay,
            )
            await asyncio.sleep(delay)
    # Unreachable — loop either returns or raises.
    raise last_exc  # type: ignore[misc]


async def fanout_rag_specialist(
    agent: LlmAgent,
    questions: list[str],
    *,
    user_id: str,
    concurrency: int = DISPATCH_CONCURRENCY,
) -> list[tuple[str, Any]]:
    """Run rag_specialist N times in parallel, capped at `concurrency`.

    Returns a list of (text, grounding_metadata) pairs ALIGNED to the input
    questions order. A failed sub-call yields ("⚠️ ...", None) — one
    failure does not poison the batch.
    """
    sem = asyncio.Semaphore(concurrency)
    chan = DISPATCH_PROGRESS_CHAN.get()

    async def _one(idx: int, q: str) -> tuple[str, Any]:
        # Emit "running" BEFORE the semaphore so the UI sees all N questions
        # as queued/running immediately, not 4-at-a-time as the semaphore
        # admits them. Status flip "queued" -> actually-doing-work happens
        # at the same moment from the user's perspective (sub-second).
        if chan is not None:
            try:
                chan.put_nowait({
                    "phase": "start",
                    "idx": idx,
                    "question": q,
                })
            except asyncio.QueueFull:  # defensive — Queue() is unbounded by default
                pass
        async with sem:
            try:
                result = await _run_one_rag_specialist(agent, q, user_id=user_id)
                if chan is not None:
                    answer_text, _gm = result
                    try:
                        chan.put_nowait({
                            "phase": "done",
                            "idx": idx,
                            "question": q,
                            "answer": answer_text,
                        })
                    except asyncio.QueueFull:
                        pass
                return result
            except Exception as exc:  # noqa: BLE001
                log.warning("rag_specialist sub-call failed (%r): %s", q, exc)
                if chan is not None:
                    try:
                        chan.put_nowait({
                            "phase": "error",
                            "idx": idx,
                            "question": q,
                            "error": f"{type(exc).__name__}: {exc}",
                        })
                    except asyncio.QueueFull:
                        pass
                return ("_⚠️ Antwort konnte nicht erzeugt werden — bitte erneut versuchen._", None)

    return await asyncio.gather(*[_one(i, q) for i, q in enumerate(questions)])


def make_dispatch_rag_questions_tool(rag_specialist: LlmAgent) -> FunctionTool:
    """Build the orchestrator-facing FunctionTool, closed over rag_specialist.

    Produced FunctionTool signature (what the LLM sees):
        dispatch_rag_questions(questions: list[str]) -> dict
    Returns: {"answers": [{"question": str, "answer": str}, ...]}

    Side effect: appends per-call grounding chunks to
    tool_context.state["agent_grounding_chunks"] in dispatch order, with
    [N] markers in each answer text already rewritten to GLOBAL indices.
    chats.py reads agent_grounding_chunks to build the citation list.
    """

    async def dispatch_rag_questions(
        questions: list[str],
        tool_context: ToolContext,
    ) -> dict:
        """Beantwortet 2+ in sich geschlossene Sachfragen parallel.

        Verwende dieses Tool, wenn die Nutzeranfrage mehrere distinkte
        Sachfragen zu den Projektdokumenten enthaelt. Jeder Eintrag in
        `questions` MUSS in sich geschlossen sein (kein Pronomen, keine
        Bezuege zu anderen Fragen — Pronomen vorher selbst aufloesen).
        Das Tool fuehrt N rag_specialist-Aufrufe parallel aus (max. 4
        gleichzeitig) und liefert pro Frage eine Antwort mit korrekten
        globalen [N]-Zitationsmarkern, die du unveraendert in deine
        finale Antwort uebernehmen MUSST.

        Args:
            questions: Liste der zu beantwortenden Fragen, jede in sich
                       geschlossen, in der gewuenschten Reihenfolge.

        Returns:
            {"answers": [{"question": str, "answer": str}, ...]} —
            answers Liste in derselben Reihenfolge wie questions.
        """
        if not questions:
            return {"answers": []}

        user_id = tool_context._invocation_context.user_id
        results = await fanout_rag_specialist(
            rag_specialist, questions, user_id=user_id
        )

        existing_chunks = list(
            tool_context.state.get("agent_grounding_chunks", []) or []
        )
        answers: list[dict] = []
        for q, (text, gm) in zip(questions, results):
            offset = len(existing_chunks)
            if gm is not None:
                # StreamingAgentTool's static helper already does the
                # grounding_supports → [N] annotation with idx_offset.
                # Reuse it so single-call and fan-out paths use IDENTICAL
                # marker math.
                text = StreamingAgentTool._annotate_with_grounding_supports(
                    text, gm, idx_offset=offset
                )
                for c in gm.grounding_chunks or []:
                    rc = getattr(c, "retrieved_context", None)
                    if rc is None:
                        continue
                    entry = {
                        "agent": "rag_specialist",
                        "text": getattr(rc, "text", "") or "",
                        "title": getattr(rc, "title", "") or "",
                        "uri": getattr(rc, "uri", "") or "",
                    }
                    rag_chunk = getattr(rc, "rag_chunk", None)
                    if rag_chunk is not None:
                        entry["rag_chunk_text"] = getattr(rag_chunk, "text", "") or ""
                        page_span = getattr(rag_chunk, "page_span", None)
                        if page_span is not None:
                            entry["page_first"] = getattr(page_span, "first_page", None)
                            entry["page_last"] = getattr(page_span, "last_page", None)
                    existing_chunks.append(entry)
            answers.append({"question": q, "answer": text})

        tool_context.state["agent_grounding_chunks"] = existing_chunks
        return {"answers": answers}

    return FunctionTool(func=dispatch_rag_questions)
