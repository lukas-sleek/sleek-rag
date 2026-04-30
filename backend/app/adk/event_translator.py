"""Translate AdkApp.async_stream_query event dicts into our internal
discriminators (plan 19.0 T9b).

Events are JSON-serialised dicts (T0 probe 1).

Event shapes seen in probe 1:
  - Model text response:
      {"author": <agent>, "content": {"role": "model",
                                       "parts": [{"text": "..."}]},
       "finish_reason": "STOP", ...}
  - Tool call (model -> tool):
      {"author": <agent>, "content": {"role": "model",
                                       "parts": [{"function_call":
                                         {"id": "...", "name": "<tool>",
                                          "args": {...}}}]},
       "finish_reason": "STOP", ...}
  - Tool response (tool -> model):
      {"author": <agent>, "content": {"role": "user",
                                       "parts": [{"function_response":
                                         {"id": "...", "name": "<tool>",
                                          "response": {...}}}]}, ...}

`author` is at the top level. `actions.state_delta` holds tool_context
state writes (e.g. our citations). No explicit "final" event signal —
the stream ends when the runner finishes; downstream code accumulates
text deltas as it goes.
"""
from __future__ import annotations

from typing import Any


def _parts(event: dict) -> list[dict]:
    return ((event.get("content") or {}).get("parts") or [])


def event_role(event: dict) -> str | None:
    return (event.get("content") or {}).get("role")


def event_kind(event: dict) -> str:
    """Discriminator for SSE forwarding. Returns one of:
    "model_text" | "tool_call" | "tool_response" | "other".
    """
    role = event_role(event)
    parts = _parts(event)
    if role == "model":
        if any("function_call" in p for p in parts):
            return "tool_call"
        if any("text" in p and p.get("text") for p in parts):
            return "model_text"
    elif role == "user":
        if any("function_response" in p for p in parts):
            return "tool_response"
    return "other"


def event_author(event: dict) -> str | None:
    return event.get("author")


def event_text(event: dict) -> str:
    return "".join(p.get("text") or "" for p in _parts(event))


def is_v2_handoff(event: dict) -> bool:
    """True iff this is a tool_response from `run_projektanalyse_v2` whose
    response body carries the {"hand_off": "projektanalyse_v2"} sentinel."""
    if event_kind(event) != "tool_response":
        return False
    for p in _parts(event):
        fr = p.get("function_response") or {}
        if fr.get("name") == "run_projektanalyse_v2" and (
            (fr.get("response") or {}).get("hand_off") == "projektanalyse_v2"
        ):
            return True
    return False


def event_state_delta(event: dict) -> dict[str, Any]:
    return ((event.get("actions") or {}).get("state_delta") or {})
