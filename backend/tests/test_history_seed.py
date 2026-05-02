"""Plan 19.0 T8c row-to-event mapping tests."""
from __future__ import annotations

import pytest

from app.adk import history as history_module
from app.adk.history import _row_to_event, seed_session


def test_row_to_event_user():
    e = _row_to_event({"role": "user", "content": "hello"})
    assert e is not None
    assert e.author == "user"
    assert e.content.role == "user"
    assert e.content.parts[0].text == "hello"


def test_row_to_event_assistant():
    e = _row_to_event({"role": "assistant", "content": "hi"})
    assert e is not None
    assert e.author == "chat_orchestrator"
    assert e.content.role == "model"


def test_row_to_event_tool_skipped():
    assert _row_to_event({"role": "tool", "content": "x"}) is None


def test_row_to_event_empty_string_safe():
    e = _row_to_event({"role": "user", "content": ""})
    assert e is not None
    assert e.content.parts[0].text == ""


def test_row_to_event_none_content_safe():
    e = _row_to_event({"role": "user", "content": None})
    assert e is not None
    assert e.content.parts[0].text == ""


@pytest.mark.asyncio
async def test_seed_session_appends_history(monkeypatch):
    """Stub Supabase + the AdkApp to verify each historical row becomes an
    appended Event in alternating user/model order."""

    rows = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "q3"},
    ]

    monkeypatch.setattr(
        history_module, "load_history_rows", lambda *_a, **_kw: rows
    )

    appended: list = []

    class _SessSvc:
        async def get_session(self, **_kw):
            class _Sess:
                state = {}
            sess = _Sess()
            sess.id = "sess-1"
            return sess

        async def append_event(self, session, event):
            appended.append(event)

    class _App:
        _tmpl_attrs = {
            "session_service": _SessSvc(),
            "app_name": "default-app-name",
        }

        async def async_create_session(self, *, user_id):
            return {"id": "sess-1"}

    sess = await seed_session(app=_App(), user_id="u1", chat_id="c1")
    assert sess.id == "sess-1"
    # The trailing user row is the just-persisted current turn — chats.py
    # passes it via async_stream_query(message=...) instead, so seed_session
    # MUST drop it here. Otherwise the model sees the question twice (once
    # in history, once as the new message) and reasons about the duplicate.
    assert len(appended) == 4
    assert [e.content.parts[0].text for e in appended] == ["q1", "a1", "q2", "a2"]
    assert [e.content.role for e in appended] == [
        "user", "model", "user", "model",
    ]


@pytest.mark.asyncio
async def test_seed_session_keeps_trailing_assistant_row(monkeypatch):
    """If the most recent row is an assistant turn (means the previous
    turn finished cleanly and this is a fresh question against an empty
    just-created chat — or odd state), don't strip it; a missing user
    suffix means there's nothing to dedupe."""
    rows = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]
    monkeypatch.setattr(
        history_module, "load_history_rows", lambda *_a, **_kw: rows
    )
    appended: list = []

    class _SessSvc:
        async def get_session(self, **_kw):
            class _S:
                state = {}
            s = _S(); s.id = "sess-1"; return s

        async def append_event(self, session, event):
            appended.append(event)

    class _App:
        _tmpl_attrs = {"session_service": _SessSvc(), "app_name": "a"}

        async def async_create_session(self, *, user_id):
            return {"id": "sess-1"}

    await seed_session(app=_App(), user_id="u1", chat_id="c1")
    assert [e.content.parts[0].text for e in appended] == ["q1", "a1"]


@pytest.mark.asyncio
async def test_seed_session_filters_by_chat_id(monkeypatch):
    """Two chats in the same project must see only their own history. We
    fake load_history_rows to return different rows per chat_id and verify
    seed_session forwards the chat_id correctly + the seeded events match
    that chat alone (not the other chat in the same project)."""
    chat_a_rows = [
        {"role": "user", "content": "A1"},
        {"role": "assistant", "content": "answer-A1"},
        {"role": "user", "content": "A2"},  # current turn — gets stripped
    ]
    chat_b_rows = [
        {"role": "user", "content": "B-first-ever"},  # current turn — stripped
    ]
    forwarded: list[str] = []

    def fake_load(chat_id, user_id, limit=20):  # noqa: ARG001
        forwarded.append(chat_id)
        return {"chat-a": chat_a_rows, "chat-b": chat_b_rows}[chat_id]

    monkeypatch.setattr(history_module, "load_history_rows", fake_load)

    per_call_appends: list[list[str]] = []
    current: list[str] = []

    class _SessSvc:
        async def get_session(self, **_kw):
            # Mark the start of a new session by snapshotting the current
            # buffer into the per-call list and resetting.
            nonlocal current
            per_call_appends.append(current)
            current = []
            class _S:
                state = {}
            s = _S(); s.id = "sess"; return s

        async def append_event(self, session, event):
            current.append(event.content.parts[0].text)

    class _App:
        _tmpl_attrs = {"session_service": _SessSvc(), "app_name": "a"}

        async def async_create_session(self, *, user_id):
            return {"id": "sess"}

    await seed_session(app=_App(), user_id="u1", chat_id="chat-a")
    await seed_session(app=_App(), user_id="u1", chat_id="chat-b")
    # Flush the trailing call's buffer.
    per_call_appends.append(current)

    # First entry is the placeholder before any seed_session ran.
    assert per_call_appends[0] == []
    chat_a_appends, chat_b_appends = per_call_appends[1], per_call_appends[2]
    assert forwarded == ["chat-a", "chat-b"]
    # Chat A: A1 + answer-A1 (A2 is the current turn → stripped).
    assert chat_a_appends == ["A1", "answer-A1"]
    # Chat B: empty (only the current turn was in history → stripped).
    # Critically, NO chat-A messages leak into the chat-B session.
    assert chat_b_appends == []


@pytest.mark.asyncio
async def test_seed_session_handles_empty_history(monkeypatch):
    """Brand-new chat: the just-persisted user message is the only row, so
    seed_session ends up appending nothing — and the model sees only the
    `message` argument when async_stream_query runs."""
    rows = [{"role": "user", "content": "first ever question"}]
    monkeypatch.setattr(
        history_module, "load_history_rows", lambda *_a, **_kw: rows
    )
    appended: list = []

    class _SessSvc:
        async def get_session(self, **_kw):
            class _S:
                state = {}
            s = _S(); s.id = "sess-1"; return s

        async def append_event(self, session, event):
            appended.append(event)

    class _App:
        _tmpl_attrs = {"session_service": _SessSvc(), "app_name": "a"}

        async def async_create_session(self, *, user_id):
            return {"id": "sess-1"}

    await seed_session(app=_App(), user_id="u1", chat_id="c1")
    assert appended == []
