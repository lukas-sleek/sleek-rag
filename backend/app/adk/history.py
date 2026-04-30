"""Replay Supabase chat_messages into a fresh InMemory ADK session (plan 19.0 T8c).

Per turn, we build a session, append historical events, then run. This
keeps Supabase as the source of truth for chat history (research §2.5
strategy (c)) at the cost of multi-turn tool-call/response fidelity —
prior tool calls become opaque text. We're already paying that cost
under Pattern A.

Verified pattern (T0 probe 5):
  1. await app.async_create_session(user_id=) -> dict with "id"
  2. live = await sess_service.get_session(app_name=, user_id=, session_id=)
  3. await sess_service.append_event(live, Event(author=, content=...))
  4. await app.async_stream_query(message=, user_id=, session_id=)
"""
from __future__ import annotations

import asyncio

from google.adk.events import Event
from google.adk.sessions import Session
from google.genai import types
from vertexai.preview.reasoning_engines import AdkApp

from app.db import supabase


_HISTORY_LIMIT = 20


def _row_to_event(row: dict) -> Event | None:
    role = row["role"]
    if role not in ("user", "assistant"):
        return None
    return Event(
        author="user" if role == "user" else "chat_orchestrator",
        content=types.Content(
            role="user" if role == "user" else "model",
            parts=[types.Part.from_text(text=row["content"] or "")],
        ),
    )


def load_history_rows(
    chat_id: str, user_id: str, limit: int = _HISTORY_LIMIT
) -> list[dict]:
    res = (
        supabase()
        .table("chat_messages")
        .select("id,role,content,created_at")
        .eq("chat_id", chat_id)
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    rows = res.data or []
    rows.reverse()  # oldest first
    return rows


async def seed_session(*, app: AdkApp, user_id: str, chat_id: str) -> Session:
    """Create a new in-memory session, append historical events, return it.

    The just-persisted user turn is included in the seeded events; chats.py
    drops it before calling app.async_stream_query so that turn can be
    passed as the `message` argument instead.
    """
    sess_service = app._tmpl_attrs["session_service"]
    app_name = app._tmpl_attrs["app_name"]

    sess_dict = await app.async_create_session(user_id=user_id)
    session_id = sess_dict["id"] if isinstance(sess_dict, dict) else sess_dict.id

    live: Session = await sess_service.get_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )

    rows = await asyncio.to_thread(load_history_rows, chat_id, user_id)
    for r in rows:
        evt = _row_to_event(r)
        if evt is not None:
            await sess_service.append_event(live, evt)
    return live
