"""Plan 19.0 T13 integration test for the ADK-driven chat stream.

Mocks `app.adk.app_factory.get_or_build_app` to return a fake AdkApp whose
`async_stream_query` yields hard-coded ADK events. Asserts SSE frame
ordering, citation isolation per turn, v2 handoff, and the no-corpus
notice path.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import pytest

from app.routers import chats as chats_module


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, data):
        self.data = data


class _SupabaseStub:
    """Records insert calls so the test can assert persistence."""

    def __init__(self, *, corpus_name: str | None = "c1"):
        self._corpus_name = corpus_name
        self.user_inserts: list[dict] = []
        self.assistant_inserts: list[dict] = []
        self._next_message_id = "asst-msg-1"
        self._mode: str | None = None
        self._table: str | None = None
        self._pending_insert: dict | None = None

    def table(self, name):
        self._table = name
        self._mode = None
        return self

    def insert(self, row):
        self._pending_insert = row
        return self

    def select(self, _cols):
        self._mode = "select"
        return self

    def update(self, _row):
        self._mode = "update"
        return self

    def eq(self, _k, _v):
        return self

    def order(self, _col, desc=False):  # noqa: ARG002
        return self

    def limit(self, _n):
        return self

    def single(self):
        self._mode = "single"
        return self

    def execute(self):
        if self._pending_insert is not None:
            row = self._pending_insert
            self._pending_insert = None
            if row.get("role") == "user":
                self.user_inserts.append(row)
                return _Resp([{**row, "id": "user-msg-1"}])
            if row.get("role") == "assistant":
                self.assistant_inserts.append(row)
                return _Resp([{**row, "id": self._next_message_id}])
            return _Resp([{**row, "id": "row-1"}])
        if self._mode == "single" and self._table == "projects":
            return _Resp({"rag_corpus_name": self._corpus_name})
        return _Resp([])


class _FakeSession:
    def __init__(self, citations=None):
        self.id = "sess-1"
        self.state = {"citations": list(citations or [])}


class _FakeSessSvc:
    def __init__(self, citations=None):
        self._session = _FakeSession(citations)

    async def get_session(self, **_kw):
        return self._session


class _FakeApp:
    def __init__(self, *, events: list[dict], citations=None):
        self._events = events
        self._tmpl_attrs = {
            "session_service": _FakeSessSvc(citations),
            "app_name": "default-app-name",
        }

    async def async_stream_query(
        self, *, message, session_id, user_id
    ) -> AsyncIterator[dict]:
        for evt in self._events:
            yield evt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _model_text(text, author="chat_orchestrator"):
    return {
        "author": author,
        "content": {"role": "model", "parts": [{"text": text}]},
    }


def _tool_response(name, response, author="chat_orchestrator"):
    return {
        "author": author,
        "content": {
            "role": "user",
            "parts": [{
                "function_response": {"id": "1", "name": name, "response": response}
            }],
        },
    }


async def _collect(gen) -> list[str]:
    return [frame async for frame in gen]


def _parse_sse(frames: list[str]) -> list[dict]:
    out = []
    for f in frames:
        s = f.strip()
        if not s.startswith("data: "):
            continue
        body = s[len("data: "):]
        if body == "[DONE]":
            continue
        out.append(json.loads(body))
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def chat_stub():
    return {"id": "chat-1", "project_id": "proj-1"}


@pytest.mark.asyncio
async def test_basic_stream_emits_delta_meta_done(monkeypatch, chat_stub):
    sb = _SupabaseStub()
    monkeypatch.setattr(chats_module, "supabase", lambda: sb)

    citations = [
        {"idx": 1, "uri": "gs://a", "page_start": 1, "snippet": "a"},
        {"idx": 2, "uri": "gs://b", "page_start": 2, "snippet": "b"},
    ]
    fake_app = _FakeApp(
        events=[_model_text("Antwort[1] und[2].")],
        citations=citations,
    )

    async def fake_get_or_build(_corpus):
        return fake_app

    async def fake_seed(*, app, user_id, chat_id):
        return _FakeSession(citations)

    monkeypatch.setattr(chats_module, "get_or_build_app", fake_get_or_build)
    monkeypatch.setattr(chats_module, "seed_session", fake_seed)

    frames = await _collect(
        chats_module._send_message_stream(
            chat=chat_stub,
            text="hi",
            chat_id="chat-1",
            user_id="u1",
            template=None,
        )
    )
    parsed = _parse_sse(frames)
    types_seen = [p["type"] for p in parsed]
    assert types_seen == ["delta", "meta", "done"]
    assert parsed[0]["content"] == "Antwort[1] und[2]."
    assert parsed[1]["citations"] == citations
    assert parsed[1]["content"] == "Antwort[1] und[2]."  # remap is identity here
    assert sb.user_inserts and sb.user_inserts[0]["content"] == "hi"
    assert sb.assistant_inserts


@pytest.mark.asyncio
async def test_filters_subagent_text(monkeypatch, chat_stub):
    sb = _SupabaseStub()
    monkeypatch.setattr(chats_module, "supabase", lambda: sb)

    fake_app = _FakeApp(
        events=[
            _model_text("internal", author="rag_specialist"),
            _model_text("Final answer."),
        ],
    )

    async def fake_get_or_build(_corpus):
        return fake_app

    async def fake_seed(*, app, user_id, chat_id):
        return _FakeSession()

    monkeypatch.setattr(chats_module, "get_or_build_app", fake_get_or_build)
    monkeypatch.setattr(chats_module, "seed_session", fake_seed)

    frames = await _collect(
        chats_module._send_message_stream(
            chat=chat_stub, text="hi", chat_id="chat-1", user_id="u1", template=None,
        )
    )
    parsed = _parse_sse(frames)
    deltas = [p for p in parsed if p["type"] == "delta"]
    assert len(deltas) == 1
    assert deltas[0]["content"] == "Final answer."


@pytest.mark.asyncio
async def test_v2_handoff_aborts_orchestrator_stream(monkeypatch, chat_stub):
    sb = _SupabaseStub()
    monkeypatch.setattr(chats_module, "supabase", lambda: sb)

    fake_app = _FakeApp(
        events=[
            _model_text("preamble"),
            _tool_response("run_projektanalyse_v2", {"hand_off": "projektanalyse_v2"}),
            _model_text("SHOULD NOT APPEAR"),
        ],
    )

    async def fake_get_or_build(_corpus):
        return fake_app

    async def fake_seed(*, app, user_id, chat_id):
        return _FakeSession()

    async def fake_v2(*, template, chat_id, user_id):
        yield "data: " + json.dumps({"type": "delta", "content": "v2-output"}) + "\n\n"
        yield "data: " + json.dumps({"type": "done"}) + "\n\n"

    monkeypatch.setattr(chats_module, "get_or_build_app", fake_get_or_build)
    monkeypatch.setattr(chats_module, "seed_session", fake_seed)
    monkeypatch.setattr(chats_module, "stream_projektanalyse_v2", fake_v2)

    frames = await _collect(
        chats_module._send_message_stream(
            chat=chat_stub, text="vollanalyse", chat_id="chat-1", user_id="u1",
            template=["q?"],
        )
    )
    parsed = _parse_sse(frames)
    contents = [p.get("content") for p in parsed if p["type"] == "delta"]
    assert "preamble" in contents
    assert "v2-output" in contents
    assert "SHOULD NOT APPEAR" not in contents


@pytest.mark.asyncio
async def test_no_corpus_friendly_notice(monkeypatch, chat_stub):
    sb = _SupabaseStub(corpus_name=None)
    monkeypatch.setattr(chats_module, "supabase", lambda: sb)

    frames = await _collect(
        chats_module._send_message_stream(
            chat=chat_stub, text="hi", chat_id="chat-1", user_id="u1", template=None,
        )
    )
    parsed = _parse_sse(frames)
    assert parsed[0]["type"] == "delta"
    assert "Dokumente" in parsed[0]["content"]
    assert parsed[1]["type"] == "meta"
    assert parsed[1]["citations"] == []
    assert parsed[2]["type"] == "done"


@pytest.mark.asyncio
async def test_citations_dedupe_renumbers_refs(monkeypatch, chat_stub):
    sb = _SupabaseStub()
    monkeypatch.setattr(chats_module, "supabase", lambda: sb)

    citations = [
        {"idx": 1, "uri": "gs://a", "page_start": 1, "snippet": "alpha"},
        {"idx": 2, "uri": "gs://b", "page_start": 2, "snippet": "bravo"},
        {"idx": 3, "uri": "gs://a", "page_start": 1, "snippet": "alpha"},  # dup
    ]
    fake_app = _FakeApp(
        events=[_model_text("X[1] Y[2] Z[3].")],
        citations=citations,
    )

    async def fake_get_or_build(_corpus):
        return fake_app

    async def fake_seed(*, app, user_id, chat_id):
        return _FakeSession(citations)

    monkeypatch.setattr(chats_module, "get_or_build_app", fake_get_or_build)
    monkeypatch.setattr(chats_module, "seed_session", fake_seed)

    frames = await _collect(
        chats_module._send_message_stream(
            chat=chat_stub, text="hi", chat_id="chat-1", user_id="u1", template=None,
        )
    )
    parsed = _parse_sse(frames)
    meta = next(p for p in parsed if p["type"] == "meta")
    assert len(meta["citations"]) == 2  # one duplicate collapsed
    assert meta["content"] == "X[1] Y[2] Z[1]."
