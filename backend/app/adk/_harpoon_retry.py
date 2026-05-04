"""Shared retry primitive for the Vertex AI Managed Vector Search "Harpoon"
transient — `URL_REJECTED Reason: 54` from `harpoon-vertex-rag-managed-
vertex-vector-search`, surfaced as `genai.errors.ClientError: 400
FAILED_PRECONDITION` with a misleading "QPS or BW…quota exceeded" event.

Confirmed Google-side flakiness in the shared serverless-RAG serving fleet
(reproduces inside Agent Builder UI). Same query reliably succeeds after a
few seconds; the only effective fix is silent retry-with-backoff. See
`.agent/incidents/2026-05-04_vertex_url_rejected_reason_54.md`.

Existing `HttpRetryOptions` on every LlmAgent (see `backend/app/adk/agents.py`)
only catch HTTP 429/500–504; Harpoon surfaces as **400** so it bypasses them.

Design: works on async iterators because that's what every place we need
to wrap actually calls (`async for event in runner.run_async(...)` in
`StreamingAgentTool` and `dispatch_rag_questions_tool`,
`app.async_stream_query(...)` in `chats.py`). Retries fire only **before
the first event has been yielded** — once events have crossed into the
caller, retrying would double up content.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import AsyncIterator, Awaitable, Callable, TypeVar

T = TypeVar("T")

# Substrings that mark the Harpoon transient. Match against `str(exc)`.
HARPOON_FINGERPRINTS: tuple[str, ...] = (
    "URL_REJECTED",
    "harpoon-vertex-rag-managed",
    "Harpoon FetchReply",
    "Failed to process Rag Managed Vertex Vector Search response",
)

DEFAULT_MAX_ATTEMPTS = 6
DEFAULT_BASE_DELAY = 1.5  # seconds — exp backoff: 1.5, 3, 6, 12, 24 → ~47s worst case


def is_harpoon_transient(exc: BaseException) -> bool:
    s = str(exc)
    return any(fp in s for fp in HARPOON_FINGERPRINTS)


def harpoon_backoff_delay(attempt: int, base_delay: float = DEFAULT_BASE_DELAY) -> float:
    """Exponential backoff with jitter. attempt is 0-indexed."""
    return base_delay * (2 ** attempt) + random.uniform(0, 0.5)


async def retry_async_iter(
    factory: Callable[[], AsyncIterator[T]],
    *,
    label: str,
    log: logging.Logger,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    on_retry: Callable[[int], Awaitable[None]] | None = None,
) -> AsyncIterator[T]:
    """Iterate `factory()` and yield events. On Harpoon transient before any
    event has been yielded, sleep + recreate the iterator + retry.

    `on_retry(attempt)` runs between attempts (e.g. to reseed sessions).
    Caller supplies a fresh iterator each time via `factory`, so no shared
    state from the failed iteration leaks into the retry.
    """
    emitted_any = False
    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            agen = factory()
            async for ev in agen:
                emitted_any = True
                yield ev
            return  # iterator finished cleanly
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if emitted_any:
                # Already streamed events — retry would double up. Bail.
                raise
            if not is_harpoon_transient(exc):
                raise
            if attempt + 1 >= max_attempts:
                log.warning(
                    "%s: harpoon retries exhausted (%d/%d). detail=%s",
                    label, attempt + 1, max_attempts,
                    str(exc).split("Events {")[0][:200],
                )
                raise
            delay = harpoon_backoff_delay(attempt, base_delay)
            log.warning(
                "%s: harpoon transient; retry %d/%d in %.1fs. detail=%s",
                label, attempt + 1, max_attempts, delay,
                str(exc).split("Events {")[0][:200],
            )
            await asyncio.sleep(delay)
            if on_retry is not None:
                try:
                    await on_retry(attempt + 1)
                except Exception as hook_exc:  # noqa: BLE001
                    log.warning(
                        "%s: on_retry hook failed: %s; aborting retries.",
                        label, hook_exc,
                    )
                    raise
    # Unreachable in practice — the loop always either yields cleanly,
    # raises a non-retryable error, or raises after exhausting attempts.
    if last_exc is not None:
        raise last_exc
