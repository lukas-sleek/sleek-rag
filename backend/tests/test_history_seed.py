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
    assert len(appended) == 5
    assert [e.content.role for e in appended] == [
        "user", "model", "user", "model", "user"
    ]
