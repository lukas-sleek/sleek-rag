"""Shared retry primitive for transient upstream failures (Vertex / Gemini
serving fleet). Originally written for the Vertex AI Managed Vector Search
"Harpoon" transient (`URL_REJECTED Reason: 54` from `harpoon-vertex-rag-
managed-vertex-vector-search`, surfaced as `genai.errors.ClientError: 400
FAILED_PRECONDITION` with a misleading "QPS or BW…" event), now broadened
to cover the full transient family the Gemini stack throws under load:

  - 503 UNAVAILABLE  ("Service unavailable", "model is overloaded")
  - 429 RESOURCE_EXHAUSTED (rate-limit / quota pressure)
  - 500 INTERNAL  / 502 / 504
  - 408 / DEADLINE_EXCEEDED
  - the original Harpoon FAILED_PRECONDITION transient (HTTP 400 but
    Google-side serving flake — survives 4xx by fingerprint match)
  - generic socket / TLS resets seen during long streams

Confirmed Google-side flakiness in the shared serverless-RAG serving fleet.
Same query reliably succeeds after a few seconds; the only effective fix
is silent retry-with-backoff. See
`.agent/incidents/2026-05-04_vertex_url_rejected_reason_54.md`.

Existing `HttpRetryOptions` on every LlmAgent (see `backend/app/adk/agents.py`)
only catch a subset; this module is the catch-all that wraps the outer
turn and the per-question fan-out so the user never sees a "could not
generate" message for transient capacity issues.

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

# Substrings that mark the original Harpoon transient (HTTP 400
# FAILED_PRECONDITION but actually a Google-side serving flake — must be
# matched explicitly because it bypasses status-code heuristics).
HARPOON_FINGERPRINTS: tuple[str, ...] = (
    "URL_REJECTED",
    "harpoon-vertex-rag-managed",
    "Harpoon FetchReply",
    "Failed to process Rag Managed Vertex Vector Search response",
)

# Substrings that mark a generic upstream transient. Matched against
# `str(exc)` after the Harpoon and status-code checks miss. Keep narrow
# enough that genuine 4xx (auth, bad request, not found) do NOT match.
TRANSIENT_FINGERPRINTS: tuple[str, ...] = (
    # gRPC status names — unambiguous, only appear in genai/grpc error formatting.
    "UNAVAILABLE",
    "RESOURCE_EXHAUSTED",
    "DEADLINE_EXCEEDED",
    "INTERNAL",
    "ABORTED",
    # Common phrasing from the Gemini API on capacity pressure.
    "overloaded",
    "Service Unavailable",
    "temporarily unavailable",
    "try again later",
    "Try again later",
    # Network / socket resets that surface mid-stream.
    "Connection reset",
    "Connection aborted",
    "Connection broken",
    "ConnectionResetError",
    "RemoteProtocolError",
    "ReadTimeout",
    "Read timed out",
    "ServerDisconnectedError",
    "EOF occurred in violation",
)

# HTTP status codes that mean "try again later". 408 (timeout), 429 (rate),
# 500/502/503/504 (server-side). Checked against `exc.code` /
# `exc.status_code` — google.genai.errors.APIError exposes `.code`.
TRANSIENT_HTTP_CODES: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})

DEFAULT_MAX_ATTEMPTS = 20
DEFAULT_BASE_DELAY = 1.5  # exp backoff base
DEFAULT_MAX_DELAY = 20.0  # per-attempt delay cap so 20-attempt budget stays sane
# 20 attempts, exp-with-cap: 1.5, 3, 6, 12, 20, 20, 20, ... → worst-case
# wall-clock ≈ 1.5+3+6+12 + 16*20 ≈ 343s (~5.7min). Within frontend
# stream patience and well under typical reverse-proxy idle timeouts.


def _exc_status_code(exc: BaseException) -> int | None:
    """Best-effort extraction of an HTTP status code from a genai/requests/
    httpx-style exception. Returns None if no numeric code is exposed."""
    for attr in ("code", "status_code", "status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    # google.genai.errors.APIError sometimes wraps status under .response.
    resp = getattr(exc, "response", None)
    if resp is not None:
        v = getattr(resp, "status_code", None) or getattr(resp, "status", None)
        if isinstance(v, int):
            return v
    return None


def is_harpoon_transient(exc: BaseException) -> bool:
    """True iff `exc` is a transient upstream failure that should be retried.

    Name kept for backward compatibility with existing call sites; predicate
    is now broader than the original Harpoon-only check (see module docstring).
    """
    s = str(exc)
    if any(fp in s for fp in HARPOON_FINGERPRINTS):
        return True
    code = _exc_status_code(exc)
    if code is not None and code in TRANSIENT_HTTP_CODES:
        return True
    if any(fp in s for fp in TRANSIENT_FINGERPRINTS):
        return True
    return False


# Public alias with a name that reflects the broadened semantics. New code
# should prefer `is_transient_upstream`; the harpoon-named function is kept
# so existing imports don't churn.
is_transient_upstream = is_harpoon_transient


def harpoon_backoff_delay(
    attempt: int,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
) -> float:
    """Exponential backoff with jitter, capped at `max_delay`. attempt is 0-indexed."""
    raw = base_delay * (2 ** attempt)
    return min(raw, max_delay) + random.uniform(0, 0.5)


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
