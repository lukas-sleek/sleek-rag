"""Plan 18.3 T9: integration test for the Pattern A chat stream.

Mocks the google-genai chat session + supabase + projektanalyse hand-off
streamers, then drives `_send_message_stream` and asserts SSE frame
ordering and projektanalyse routing.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
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
    """Records insert/select calls so the test can assert persistence."""

    def __init__(self, history_rows=None):
        self._history_rows = history_rows or []
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

    def eq(self, _k, _v):
        return self

    def order(self, _col, desc=False):  # noqa: ARG002
        return self

    def limit(self, _n):
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
        if self._mode == "select" and self._table == "chat_messages":
            return _Resp(self._history_rows)
        return _Resp([])


class _FakeChunk:
    """Minimal stand-in for google.genai.types.GenerateContentResponse chunks.

    Mirrors the genai response shape the production code reads — text comes
    from `candidates[0].content.parts[*].text`, not the convenience
    `.text` property (which can raise on function-call-only chunks)."""

    def __init__(self, text=None, function_call=None, candidates=None):
        if candidates is not None:
            self.candidates = candidates
            return
        parts: list[SimpleNamespace] = []
        if text is not None:
            parts.append(SimpleNamespace(text=text, function_call=None))
        if function_call is not None:
            parts.append(SimpleNamespace(text=None, function_call=function_call))
        if parts:
            self.candidates = [
                SimpleNamespace(
                    content=SimpleNamespace(parts=parts),
                    grounding_metadata=None,
                )
            ]
        else:
            self.candidates = []


class _FakeChatSession:
    def __init__(self, chunks):
        self._chunks = chunks

    async def send_message_stream(self, _text):
        async def _gen():
            for ch in self._chunks:
                yield ch

        return _gen()


class _FakeAioChats:
    def __init__(self, chunks):
        self._chunks = chunks
        self.create_calls: list[dict] = []

    def create(self, *, model, config, history):
        self.create_calls.append(
            {"model": model, "config": config, "history": history}
        )
        return _FakeChatSession(self._chunks)


class _FakeClient:
    def __init__(self, chunks):
        self.aio = SimpleNamespace(chats=_FakeAioChats(chunks))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drain_async(agen) -> list[dict]:
    out: list[dict] = []

    async def _go():
        async for sse in agen:
            assert sse.startswith("data: "), sse
            out.append(json.loads(sse[len("data: ") :].strip()))

    asyncio.run(_go())
    return out


def _wire_common(monkeypatch, chunks, stub: _SupabaseStub):
    monkeypatch.setattr(chats_module, "supabase", lambda: stub)
    fake_client = _FakeClient(chunks)
    monkeypatch.setattr(chats_module, "_client", lambda: fake_client)
    # Build a no-op config so we don't read real project rows.
    monkeypatch.setattr(
        chats_module,
        "_build_config",
        lambda project_id: SimpleNamespace(project_id=project_id),
    )

    async def _no_citations(_resp, _project_id):
        return []

    monkeypatch.setattr(chats_module, "grounding_to_citations", _no_citations)
    return fake_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_streams_deltas_then_meta_then_done(monkeypatch):
    """Plan 18.3 §SSE order: deltas first, then meta with citations, then done."""
    chunks = [
        _FakeChunk(text="Hallo "),
        _FakeChunk(text="Welt!"),
    ]
    stub = _SupabaseStub()
    _wire_common(monkeypatch, chunks, stub)

    frames = _drain_async(
        chats_module._send_message_stream(
            chat={"project_id": "p1"},
            text="Frage?",
            chat_id="c1",
            user_id="u1",
            template=None,
        )
    )

    types_seen = [f["type"] for f in frames]
    assert types_seen == ["delta", "delta", "meta", "done"]
    assert frames[0]["content"] == "Hallo "
    assert frames[1]["content"] == "Welt!"
    assert frames[2]["citations"] == []
    assert frames[3]["message_id"] == "asst-msg-1"

    # User + assistant rows persisted.
    assert stub.user_inserts and stub.user_inserts[0]["content"] == "Frage?"
    assert stub.assistant_inserts
    assert stub.assistant_inserts[0]["content"] == "Hallo Welt!"
    assert stub.assistant_inserts[0]["citations"] == []


def test_routes_run_projektanalyse_function_call(monkeypatch):
    """Plan 18.3 T6: function_call run_projektanalyse hands the rest of the
    SSE stream to stream_projektanalyse and returns. No grounding meta
    frame is emitted on this path — the v1 streamer owns the close-out."""
    fc = SimpleNamespace(name="run_projektanalyse", args={})
    chunks = [_FakeChunk(function_call=fc)]
    stub = _SupabaseStub()
    _wire_common(monkeypatch, chunks, stub)

    captured: list[str] = []

    async def _fake_v1_stream(*, template, chat_id, user_id):
        captured.append(f"{chat_id}|{user_id}|{template}")
        yield 'data: {"type":"delta","content":"v1-report"}\n\n'
        yield 'data: {"type":"done","message_id":"v1-msg"}\n\n'

    monkeypatch.setattr(chats_module, "stream_projektanalyse", _fake_v1_stream)

    frames = _drain_async(
        chats_module._send_message_stream(
            chat={"project_id": "p1"},
            text="Erstelle Projektanalyse",
            chat_id="c1",
            user_id="u1",
            template=["Was ist X?"],
        )
    )

    assert captured == ["c1|u1|['Was ist X?']"]
    assert frames == [
        {"type": "delta", "content": "v1-report"},
        {"type": "done", "message_id": "v1-msg"},
    ]
    # No assistant message persisted on the chat path — v1 streamer owns persistence.
    assert not stub.assistant_inserts


def test_routes_run_projektanalyse_v2_function_call(monkeypatch):
    fc = SimpleNamespace(name="run_projektanalyse_v2", args={})
    chunks = [_FakeChunk(function_call=fc)]
    stub = _SupabaseStub()
    _wire_common(monkeypatch, chunks, stub)

    captured: list[str] = []

    async def _fake_v2_stream(*, template, chat_id, user_id):
        captured.append(f"{chat_id}|{user_id}|{template}")
        yield 'data: {"type":"delta","content":"v2-report"}\n\n'
        yield 'data: {"type":"done","message_id":"v2-msg"}\n\n'

    monkeypatch.setattr(chats_module, "stream_projektanalyse_v2", _fake_v2_stream)

    frames = _drain_async(
        chats_module._send_message_stream(
            chat={"project_id": "p1"},
            text="Projektanalyse v2 erstellen",
            chat_id="c1",
            user_id="u1",
            template=["Q?"],
        )
    )

    assert captured == ["c1|u1|['Q?']"]
    assert [f["type"] for f in frames] == ["delta", "done"]


def test_stream_error_emits_friendly_banner(monkeypatch):
    """Upstream Gemini failures must surface a German banner, no persistence."""

    class _BoomChats:
        def create(self, *, model, config, history):  # noqa: ARG002
            class _BoomSession:
                async def send_message_stream(self, _text):
                    raise RuntimeError("upstream 503")

            return _BoomSession()

    fake_client = SimpleNamespace(aio=SimpleNamespace(chats=_BoomChats()))
    stub = _SupabaseStub()
    monkeypatch.setattr(chats_module, "supabase", lambda: stub)
    monkeypatch.setattr(chats_module, "_client", lambda: fake_client)
    monkeypatch.setattr(
        chats_module,
        "_build_config",
        lambda project_id: SimpleNamespace(project_id=project_id),
    )

    async def _no_citations(_resp, _project_id):
        return []

    monkeypatch.setattr(chats_module, "grounding_to_citations", _no_citations)

    frames = _drain_async(
        chats_module._send_message_stream(
            chat={"project_id": "p1"},
            text="Frage?",
            chat_id="c1",
            user_id="u1",
            template=None,
        )
    )

    types_seen = [f["type"] for f in frames]
    assert types_seen == ["delta", "meta", "done"]
    assert "Antwort konnte gerade nicht erzeugt werden" in frames[0]["content"]
    # Don't persist a banner-only assistant turn — would poison subsequent
    # turns in this chat (history-poisoning cascade noted in old loop).
    assert not stub.assistant_inserts


def test_picks_latest_grounded_chunk_not_final_chunk(monkeypatch):
    """Vertex emits grounding_metadata on the chunk that completes retrieval —
    typically mid-stream, not on the trailing finish chunk. Citations must
    come from the last grounded chunk we saw, not blindly from the final one
    (which often has empty metadata and would yield zero citations)."""
    grounded_meta = SimpleNamespace(
        grounding_chunks=[SimpleNamespace(retrieved_context=SimpleNamespace())]
    )
    grounded_candidate = SimpleNamespace(
        content=SimpleNamespace(parts=[SimpleNamespace(text="grounded delta", function_call=None)]),
        grounding_metadata=grounded_meta,
    )
    grounded_chunk = SimpleNamespace(candidates=[grounded_candidate])

    trailing_candidate = SimpleNamespace(
        content=SimpleNamespace(parts=[]),
        grounding_metadata=None,
    )
    trailing_chunk = SimpleNamespace(candidates=[trailing_candidate])

    chunks = [_FakeChunk(text="hi "), grounded_chunk, trailing_chunk]
    stub = _SupabaseStub()
    _wire_common(monkeypatch, chunks, stub)

    captured: list[object] = []

    async def _capturing_extractor(resp, _project_id):
        captured.append(resp)
        return [{"chunk_id": "fake", "filename": "f.pdf"}]

    monkeypatch.setattr(chats_module, "grounding_to_citations", _capturing_extractor)

    frames = _drain_async(
        chats_module._send_message_stream(
            chat={"project_id": "p1"},
            text="Frage?",
            chat_id="c1",
            user_id="u1",
            template=None,
        )
    )

    assert captured == [grounded_chunk]
    meta_frame = next(f for f in frames if f["type"] == "meta")
    assert meta_frame["citations"] == [{"chunk_id": "fake", "filename": "f.pdf"}]


def test_history_drops_just_inserted_user_turn(monkeypatch):
    """If supabase returns the just-inserted user message in the history
    rows, it must be excluded from the genai history (it's passed as the
    `text` argument to send_message_stream instead)."""
    history_rows = [
        # newest-first per `.order(..., desc=True)`
        {"role": "user", "content": "Frage?", "created_at": "t3"},
        {"role": "assistant", "content": "Vorherige Antwort.", "created_at": "t2"},
        {"role": "user", "content": "Vorherige Frage.", "created_at": "t1"},
    ]
    chunks = [_FakeChunk(text="OK")]
    stub = _SupabaseStub(history_rows=history_rows)
    fake_client = _wire_common(monkeypatch, chunks, stub)

    _drain_async(
        chats_module._send_message_stream(
            chat={"project_id": "p1"},
            text="Frage?",
            chat_id="c1",
            user_id="u1",
            template=None,
        )
    )

    assert fake_client.aio.chats.create_calls
    history = fake_client.aio.chats.create_calls[0]["history"]
    assert len(history) == 2
    # genai role mapping: assistant → "model", user → "user"
    assert history[0].role == "user"
    assert history[1].role == "model"
