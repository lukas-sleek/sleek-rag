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
    def __init__(self, grounding_chunks=None):
        self.id = "sess-1"
        # Native vertex_rag_store retrieval surfaces citations via
        # state["agent_grounding_chunks"] (set by StreamingAgentTool when
        # propagate_grounding_metadata=True). chats.py turns each entry
        # into a [N] citation record at end-of-turn.
        self.state = {
            "agent_grounding_chunks": list(grounding_chunks or []),
        }


class _FakeSessSvc:
    def __init__(self, grounding_chunks=None):
        self._session = _FakeSession(grounding_chunks)

    async def get_session(self, **_kw):
        return self._session


class _FakeApp:
    def __init__(self, *, events: list[dict], grounding_chunks=None):
        self._events = events
        self._tmpl_attrs = {
            "session_service": _FakeSessSvc(grounding_chunks),
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


def _model_thought_and_text(thought, text, author="chat_orchestrator"):
    """Mirrors what genai emits when ThinkingConfig.include_thoughts=True:
    thought parts have `thought: True`, answer parts don't."""
    return {
        "author": author,
        "content": {
            "role": "model",
            "parts": [
                {"text": thought, "thought": True},
                {"text": text},
            ],
        },
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


def _model_thought_and_tool_call(thought, tool_name, args, author="chat_orchestrator"):
    """Gemini bundles the orchestrator's pre-tool-call planning chain-of-thought
    into the SAME event as the function_call. We must surface that thought in
    the activity panel, otherwise the user only sees the bare tool_call and
    thinks the orchestrator did no planning."""
    return {
        "author": author,
        "content": {
            "role": "model",
            "parts": [
                {"text": thought, "thought": True},
                {"function_call": {"id": "1", "name": tool_name, "args": args}},
            ],
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

    # Two grounding chunks → two [N] citations in retrieval order.
    grounding_chunks = [
        {"agent": "rag_specialist", "uri": "gs://b/a.pdf",
         "title": "a.pdf", "text": "alpha snippet"},
        {"agent": "rag_specialist", "uri": "gs://b/b.pdf",
         "title": "b.pdf", "text": "bravo snippet"},
    ]
    fake_app = _FakeApp(
        events=[_model_text("Antwort[1] und[2].")],
        grounding_chunks=grounding_chunks,
    )

    async def fake_get_or_build(_corpus):
        return fake_app

    async def fake_seed(*, app, user_id, chat_id):
        return _FakeSession(grounding_chunks)

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
    citations = parsed[1]["citations"]
    assert [c["idx"] for c in citations] == [1, 2]
    assert [c["filename"] for c in citations] == ["a.pdf", "b.pdf"]
    assert [c["snippet"] for c in citations] == ["alpha snippet", "bravo snippet"]
    assert all(c["kind"] == "file" for c in citations)
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
    """Multi-question fan-out: rag_specialist runs twice, each call appends
    its grounding chunks. The same source URI retrieved by both calls
    becomes one duplicate that dedupe_and_renumber collapses, and any
    [N] in the model's text that pointed at the dup gets remapped."""
    sb = _SupabaseStub()
    monkeypatch.setattr(chats_module, "supabase", lambda: sb)

    grounding_chunks = [
        {"agent": "rag_specialist", "uri": "gs://b/a.pdf",
         "title": "a.pdf", "text": "alpha"},
        {"agent": "rag_specialist", "uri": "gs://b/b.pdf",
         "title": "b.pdf", "text": "bravo"},
        # Same URI as #1 — should collapse via the chunk_id (which encodes
        # the URI) once dedupe_and_renumber runs.
        {"agent": "rag_specialist", "uri": "gs://b/a.pdf",
         "title": "a.pdf", "text": "alpha"},
    ]
    fake_app = _FakeApp(
        events=[_model_text("X[1] Y[2] Z[3].")],
        grounding_chunks=grounding_chunks,
    )

    async def fake_get_or_build(_corpus):
        return fake_app

    async def fake_seed(*, app, user_id, chat_id):
        return _FakeSession(grounding_chunks)

    monkeypatch.setattr(chats_module, "get_or_build_app", fake_get_or_build)
    monkeypatch.setattr(chats_module, "seed_session", fake_seed)

    frames = await _collect(
        chats_module._send_message_stream(
            chat=chat_stub, text="hi", chat_id="chat-1", user_id="u1", template=None,
        )
    )
    parsed = _parse_sse(frames)
    meta = next(p for p in parsed if p["type"] == "meta")
    # Two unique sources after collapse.
    assert len(meta["citations"]) == 2
    # [3] remaps to the surviving idx for the duplicate URI.
    assert "Z[1]" in meta["content"] or "Z[2]" in meta["content"]


def test_build_trace_frames_search_project_documents_emits_chunks():
    """Activity panel must receive structured chunks (idx/filename/score/snippet)
    for search_project_documents tool_responses, not the truncated JSON blob."""
    long_text = "x" * 600  # would normally be cut off by the 400-char preview
    event = _tool_response(
        "search_project_documents",
        {
            "status": "ok",
            "chunks": [
                {"idx": 1, "filename": "a.pdf", "text": long_text, "score": 0.82},
                {"idx": 2, "filename": "b.pdf", "text": "short hit", "score": 0.31},
                {"idx": 3, "filename": "c.pdf", "text": "no score", "score": None},
            ],
        },
    )
    frames = chats_module._build_trace_frames(event, next_id=10)
    assert len(frames) == 1
    f = frames[0]
    assert f["name"] == "search_project_documents"
    assert f["status"] == "ok"
    # No truncated-JSON blob — replaced by structured chunks
    assert "response" not in f
    assert [c["idx"] for c in f["chunks"]] == [1, 2, 3]
    assert [c["score"] for c in f["chunks"]] == [0.82, 0.31, None]
    # Snippet truncated to ~240 chars regardless of original size
    assert len(f["chunks"][0]["snippet"]) <= 240
    assert f["chunks"][2]["filename"] == "c.pdf"


def test_build_trace_frames_other_tools_keep_truncated_response():
    """Non-retrieval tool_responses keep the generic truncated JSON-blob
    preview (no chunks field)."""
    event = _tool_response("web_researcher", {"result": "antwort"})
    frames = chats_module._build_trace_frames(event, next_id=1)
    assert len(frames) == 1
    f = frames[0]
    assert f["name"] == "web_researcher"
    assert "chunks" not in f
    assert "antwort" in f["response"]


def test_build_sub_agent_trace_frames_emits_one_per_new_seq():
    """StreamingAgentTool appends to state['agent_trace'] with monotonic
    seq ids. The chats.py builder must emit one frame per *new* seq,
    preserving the sub-agent name as the frame author, and skip seqs
    already rendered earlier in the turn."""
    state_delta = {
        "agent_trace": [
            {"agent": "rag_specialist", "kind": "model_thought",
             "text": "scanne nach Projektleiter", "seq": 0},
            {"agent": "rag_specialist", "kind": "model_thought",
             "text": "fasse Treffer zusammen", "seq": 1},
        ],
    }
    frames, seen = chats_module._build_sub_agent_trace_frames(
        state_delta, seen=set(), next_id=10,
    )
    assert [f["kind"] for f in frames] == ["model_thought", "model_thought"]
    assert [f["author"] for f in frames] == ["rag_specialist", "rag_specialist"]
    assert frames[0]["text"] == "scanne nach Projektleiter"
    assert seen == {0, 1}

    # Next event delivers the cumulative list again + a new entry.
    state_delta2 = {
        "agent_trace": [
            {"agent": "rag_specialist", "kind": "model_thought",
             "text": "scanne nach Projektleiter", "seq": 0},
            {"agent": "rag_specialist", "kind": "model_thought",
             "text": "fasse Treffer zusammen", "seq": 1},
            {"agent": "document_retriever", "kind": "tool_call",
             "name": "search_project_documents",
             "args": '{"query": "Projektleiter"}', "seq": 2},
        ],
    }
    frames2, seen2 = chats_module._build_sub_agent_trace_frames(
        state_delta2, seen=seen, next_id=20,
    )
    assert len(frames2) == 1
    assert frames2[0]["author"] == "document_retriever"
    assert frames2[0]["kind"] == "tool_call"
    assert frames2[0]["name"] == "search_project_documents"
    assert seen2 == {0, 1, 2}


def test_build_sub_agent_trace_frames_renders_tool_response_chunks():
    """search_project_documents tool_responses captured via state must keep
    the rich chunks-with-scores rendering, not the truncated-JSON fallback,
    so confidence badges still surface inside nested calls."""
    state_delta = {
        "agent_trace": [
            {
                "agent": "document_retriever",
                "kind": "tool_response",
                "name": "search_project_documents",
                "seq": 5,
                "response": {
                    "status": "ok",
                    "chunks": [
                        {"idx": 1, "filename": "a.pdf", "text": "x" * 600,
                         "score": 0.91},
                        {"idx": 2, "filename": "b.pdf", "text": "kurz",
                         "score": 0.42},
                    ],
                },
            },
        ],
    }
    frames, _ = chats_module._build_sub_agent_trace_frames(
        state_delta, seen=set(), next_id=1,
    )
    assert len(frames) == 1
    f = frames[0]
    assert f["kind"] == "tool_response"
    assert f["name"] == "search_project_documents"
    assert f["status"] == "ok"
    assert "response" not in f
    assert [c["score"] for c in f["chunks"]] == [0.91, 0.42]
    assert len(f["chunks"][0]["snippet"]) <= 240


def test_build_sub_agent_trace_frames_no_trace_returns_empty():
    frames, seen = chats_module._build_sub_agent_trace_frames(
        {}, seen=set(), next_id=1,
    )
    assert frames == []
    assert seen == set()


def test_build_trace_frames_emits_thought_before_tool_call():
    """Orchestrator's inline thought (planning before invoking the tool) must
    surface as a `model_thought` frame BEFORE the `tool_call` frame. Without
    this, the orchestrator looks like it does no planning at all."""
    event = _model_thought_and_tool_call(
        "ich pruefe was der Nutzer braucht und delegiere an rag_specialist",
        "rag_specialist",
        {"request": "Wer ist der Projektleiter?"},
    )
    frames = chats_module._build_trace_frames(event, next_id=1)
    assert len(frames) == 2
    assert frames[0]["kind"] == "model_thought"
    assert frames[0]["author"] == "chat_orchestrator"
    assert "delegiere" in frames[0]["text"]
    assert frames[1]["kind"] == "tool_call"
    assert frames[1]["name"] == "rag_specialist"


def test_build_trace_frames_splits_thought_and_answer():
    """Events from thinking-enabled agents carry both `thought=True` parts
    and answer parts. The activity panel must see them as two separate
    frames so it can render distinct headlines/icons."""
    event = _model_thought_and_text(
        "ich pruefe Quelle [3]",
        "Die Bausumme betraegt CHF 12 Mio.",
    )
    frames = chats_module._build_trace_frames(event, next_id=5)
    assert len(frames) == 2
    assert frames[0]["kind"] == "model_thought"
    assert frames[0]["text"] == "ich pruefe Quelle [3]"
    assert frames[1]["kind"] == "model_text"
    assert frames[1]["text"] == "Die Bausumme betraegt CHF 12 Mio."


@pytest.mark.asyncio
async def test_thought_parts_do_not_leak_into_user_stream(monkeypatch, chat_stub):
    """Chain-of-thought MUST stay in the activity panel — never in the
    streamed delta the user sees, and never persisted to chat_messages."""
    sb = _SupabaseStub()
    monkeypatch.setattr(chats_module, "supabase", lambda: sb)

    fake_app = _FakeApp(
        events=[
            _model_thought_and_text(
                "interner Gedanke darf nicht raus",
                "sichtbare Antwort",
            )
        ],
        grounding_chunks=[],
    )

    async def fake_get_or_build(_corpus):
        return fake_app

    async def fake_seed(*, app, user_id, chat_id):
        return _FakeSession([])

    monkeypatch.setattr(chats_module, "get_or_build_app", fake_get_or_build)
    monkeypatch.setattr(chats_module, "seed_session", fake_seed)

    frames = await _collect(
        chats_module._send_message_stream(
            chat=chat_stub, text="hi", chat_id="chat-1", user_id="u1", template=None,
        )
    )
    parsed = _parse_sse(frames)
    deltas = [p["content"] for p in parsed if p.get("type") == "delta"]
    assert deltas == ["sichtbare Antwort"]
    assert all("interner Gedanke" not in d for d in deltas)
    # Persisted assistant message must not include the thought either.
    assert sb.assistant_inserts, "assistant message was not persisted"
    for ins in sb.assistant_inserts:
        assert "interner Gedanke" not in ins["content"]
