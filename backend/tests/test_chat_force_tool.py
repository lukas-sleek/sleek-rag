"""Plan 17.3 T1+T2: dispatch-coercion in the chat agent loop.

Verifies:
  T1. When the sufficiency check returns sufficient=false on a final-text
      iteration, the next chat.completions.create call is issued with
      tool_choice="required". The flag is consumed for one turn.
  T2. When every retrieval tool dispatched in a turn returns a structured
      `error` envelope, a TOOL-RETRY system message is appended and the
      next iteration is forced to issue a tool call. The error guidance
      strings never leak into the SSE delta stream.
  T2b. After 3 consecutive all-error tool turns the loop bails with the
       generic warning text — no v2 fallback (per project rule).

Mocks the Gemini stream, supabase, and tool executors.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.routers import chats as chats_module
from app.retrieval import RetrievedChunk


def _mk_event(*, content: str | None = None, tool_calls=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    choice = SimpleNamespace(delta=delta, finish_reason=None)
    return SimpleNamespace(choices=[choice])


def _mk_tool_delta(*, idx: int, tc_id: str, name: str, args: str):
    fn = SimpleNamespace(name=name, arguments=args)
    return SimpleNamespace(index=idx, id=tc_id, function=fn)


def _mk_chunk(chunk_id: str = "c1") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        file_id="file-aaaa",
        filename="doc.pdf",
        project_id="proj-1",
        content=f"content-{chunk_id}",
        page_start=1,
        page_end=1,
        figure_label=None,
        block_type="paragraph",
        score=0.9,
    )


@pytest.fixture
def loop_harness(monkeypatch):
    """Generic harness that captures every chat.completions.create kwargs
    and lets each test specify the per-iteration event stream + tool result
    + sufficiency verdict."""
    captured: dict = {
        "create_calls": [],  # list[dict] — kwargs per chat.completions.create
        "executor_calls": [],
        "delta_frames": [],
    }

    # supabase mock — same shape as test_chat_multi_tool_loop.py.
    def fake_supabase():
        sb = MagicMock()
        chain = MagicMock()
        chain._is_insert = False

        def insert_returns(*_a, **_k):
            chain._is_insert = True
            return chain

        def select_returns(*_a, **_k):
            chain._is_insert = False
            return chain

        def execute():
            if chain._is_insert:
                chain._is_insert = False
                return SimpleNamespace(data=[{"id": "msg-1"}])
            return SimpleNamespace(data=[])

        sb.table.return_value = chain
        chain.insert.side_effect = insert_returns
        chain.select.side_effect = select_returns
        chain.eq.return_value = chain
        chain.neq.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        chain.update.return_value = chain
        chain.execute.side_effect = execute
        return sb

    monkeypatch.setattr(chats_module, "supabase", fake_supabase)
    monkeypatch.setattr(
        chats_module,
        "_build_system_message",
        lambda pid, uid: {"role": "system", "content": "sys"},
    )
    return captured


def _install_streams(monkeypatch, captured, events_per_iter):
    """Attach a fake gemini_client_untraced to chats_module that yields the
    given iteration event lists in order. Captures kwargs of each create
    call into captured['create_calls']."""
    call_idx = [0]

    def fake_create(**kwargs):
        captured["create_calls"].append({k: v for k, v in kwargs.items() if k != "stream"})
        i = call_idx[0]
        call_idx[0] += 1
        return iter(events_per_iter[i])

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=fake_create)
        )
    )
    monkeypatch.setattr(
        chats_module, "gemini_client_untraced", lambda: fake_client
    )


def _drain(agen):
    out: list[dict] = []

    async def _go():
        async for sse in agen:
            assert sse.startswith("data: ")
            payload = sse[len("data: ") :].strip()
            try:
                out.append(json.loads(payload))
            except json.JSONDecodeError:
                pass

    asyncio.run(_go())
    return out


# ---------------------------------------------------------------------------
# T1 — sufficiency=false → next iteration forced to tool call
# ---------------------------------------------------------------------------


def test_sufficiency_fail_forces_tool_choice_required(loop_harness, monkeypatch):
    """Iteration 1 emits search_chunks; iteration 2 emits final text;
    sufficiency rules insufficient; iteration 3 must be issued with
    tool_choice='required'."""
    events_per_iter = [
        # iter 0: tool call
        [
            _mk_event(
                tool_calls=[
                    _mk_tool_delta(
                        idx=0,
                        tc_id="call_search",
                        name="search_chunks",
                        args=json.dumps({"query": "Bauherren"}),
                    )
                ]
            ),
        ],
        # iter 1: final text (will be intercepted by sufficiency check)
        [_mk_event(content="Nur Hochdorf [1].")],
        # iter 2: model retries with another search call (forced)
        [
            _mk_event(
                tool_calls=[
                    _mk_tool_delta(
                        idx=0,
                        tc_id="call_retry",
                        name="search_chunks",
                        args=json.dumps({"query": "Grundeigentümer"}),
                    )
                ]
            ),
        ],
        # iter 3: final text after retry
        [_mk_event(content="Hochdorf, SBB, Manor [1][2].")],
    ]
    _install_streams(monkeypatch, loop_harness, events_per_iter)

    def fake_search(*, args, project_id, user_id, ref_offset):
        loop_harness["executor_calls"].append(("search_chunks", args, ref_offset))
        chunks = [_mk_chunk(f"c-{ref_offset}")]
        return {
            "results": [{"ref": ref_offset + 1, "chunk_id": chunks[0].chunk_id}],
            "_chunks": chunks,
        }

    monkeypatch.setattr(chats_module, "execute_search_chunks", fake_search)

    # Sufficiency: first call returns insufficient, then sufficient (it would
    # only be called twice — first after iter 1, then never again because the
    # nudge-once rule short-circuits on iter 3).
    sufficiency_calls = []

    def fake_assess(*, question, chunks):
        sufficiency_calls.append(len(chunks))
        if len(sufficiency_calls) == 1:
            return {
                "sufficient": False,
                "missing": "weitere Bauherren",
                "feedback": "search_chunks(query='Grundeigentümer')",
            }
        return {"sufficient": True, "missing": None, "feedback": None}

    monkeypatch.setattr(chats_module, "assess_sufficiency", fake_assess)

    chat = {"project_id": "proj-1", "id": "chat-1"}
    frames = _drain(
        chats_module._send_message_stream(
            chat=chat,
            text="Welche Bauherren?",
            chat_id="chat-1",
            user_id="user-1",
            template=None,
        )
    )

    create_calls = loop_harness["create_calls"]
    # 4 create calls — one per iteration.
    assert len(create_calls) == 4

    # Iter 0/1/3 — auto (no tool_choice override).
    assert "tool_choice" not in create_calls[0]
    assert "tool_choice" not in create_calls[1]
    # Iter 2 — forced because sufficiency just nudged on iter 1.
    assert create_calls[2].get("tool_choice") == "required"
    # Iter 3 — flag was consumed; back to auto.
    assert "tool_choice" not in create_calls[3]

    # Regression for the v2-fallback bug: when forced, the bound tool list
    # MUST NOT include run_projektanalyse / run_projektanalyse_v2 — otherwise
    # the model satisfies tool_choice="required" by calling v2, which is
    # exactly the auto-escalation behavior the project rule forbids.
    forced_tool_names = {
        t["function"]["name"] for t in create_calls[2]["tools"]
    }
    assert forced_tool_names == {
        "search_chunks",
        "list_document_outline",
        "read_section",
    }
    # Non-forced turns keep all 5 tools bound (so v2 stays user-elected).
    auto_tool_names = {t["function"]["name"] for t in create_calls[0]["tools"]}
    assert "run_projektanalyse_v2" in auto_tool_names
    assert "run_projektanalyse" in auto_tool_names

    # Final delta carries the iter-3 text, not the iter-1 text.
    deltas = [f["content"] for f in frames if f.get("type") == "delta"]
    assert any("Hochdorf, SBB, Manor" in d for d in deltas)
    # Iter-1's "Nur Hochdorf" should NOT be streamed to the user.
    assert not any("Nur Hochdorf" in d for d in deltas)


# ---------------------------------------------------------------------------
# T2 — structured error → TOOL-RETRY + force tool next iter
# ---------------------------------------------------------------------------


def test_all_errored_tool_calls_force_retry(loop_harness, monkeypatch):
    """When every search_chunks dispatch this turn returns an error envelope,
    the next iteration must see a TOOL-RETRY system message in `messages`
    AND be issued with tool_choice='required'. Error guidance strings must
    NOT leak into delta frames."""
    captured_messages_seen = []

    events_per_iter = [
        # iter 0: empty-query search_chunks (will hit error envelope)
        [
            _mk_event(
                tool_calls=[
                    _mk_tool_delta(
                        idx=0,
                        tc_id="call_bad",
                        name="search_chunks",
                        args=json.dumps({}),  # missing query
                    )
                ]
            ),
        ],
        # iter 1: forced; model retries with proper query
        [
            _mk_event(
                tool_calls=[
                    _mk_tool_delta(
                        idx=0,
                        tc_id="call_good",
                        name="search_chunks",
                        args=json.dumps({"query": "Termine"}),
                    )
                ]
            ),
        ],
        # iter 2: final text
        [_mk_event(content="Termine: ... [1].")],
    ]
    call_idx = [0]

    def fake_create(**kwargs):
        loop_harness["create_calls"].append(
            {k: v for k, v in kwargs.items() if k != "stream"}
        )
        # Snapshot the messages list shape on each call for assertion below.
        captured_messages_seen.append(list(kwargs.get("messages") or []))
        i = call_idx[0]
        call_idx[0] += 1
        return iter(events_per_iter[i])

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=fake_create)
        )
    )
    monkeypatch.setattr(
        chats_module, "gemini_client_untraced", lambda: fake_client
    )

    def fake_search(*, args, project_id, user_id, ref_offset):
        if not args.get("query"):
            return {
                "results": [],
                "error": {
                    "code": "missing_required_argument",
                    "argument": "query",
                    "guidance": "ÜBERSETZE die Frage in einen Such-String.",
                },
            }
        chunks = [_mk_chunk("c-good")]
        return {
            "results": [{"ref": ref_offset + 1, "chunk_id": "c-good"}],
            "_chunks": chunks,
        }

    monkeypatch.setattr(chats_module, "execute_search_chunks", fake_search)
    # No sufficiency nudge needed — chunks present, rater returns sufficient.
    monkeypatch.setattr(
        chats_module,
        "assess_sufficiency",
        lambda *, question, chunks: {
            "sufficient": True,
            "missing": None,
            "feedback": None,
        },
    )

    chat = {"project_id": "proj-1", "id": "chat-1"}
    frames = _drain(
        chats_module._send_message_stream(
            chat=chat,
            text="Welche Termine?",
            chat_id="chat-1",
            user_id="user-1",
            template=None,
        )
    )

    create_calls = loop_harness["create_calls"]
    assert len(create_calls) == 3
    # Iter 1 (after the all-errored iter 0) must be forced.
    assert create_calls[1].get("tool_choice") == "required"
    # Iter 2 (after a successful retrieval) must NOT be forced.
    assert "tool_choice" not in create_calls[2]

    # The messages list at iter-1 must contain a TOOL-RETRY system message.
    iter1_msgs = captured_messages_seen[1]
    retry_systems = [
        m for m in iter1_msgs
        if m.get("role") == "system" and "TOOL-RETRY" in (m.get("content") or "")
    ]
    assert len(retry_systems) == 1
    assert "search_chunks" in retry_systems[0]["content"]
    assert "missing_required_argument" in retry_systems[0]["content"]

    # The final delta is the iter-2 answer, NOT the error guidance.
    deltas = [f["content"] for f in frames if f.get("type") == "delta"]
    assert any("Termine" in d for d in deltas)
    assert not any("ÜBERSETZE" in d for d in deltas)


def test_three_consecutive_error_streaks_bails_with_warning(
    loop_harness, monkeypatch
):
    """Three error-only tool turns in a row → loop terminates with the
    generic warning text. No v2 escalation (per project feedback rule)."""
    # 3 iterations of all-errored tool calls; the loop must bail before
    # iter 3 ever fires.
    events_per_iter = [
        [
            _mk_event(
                tool_calls=[
                    _mk_tool_delta(
                        idx=0,
                        tc_id=f"call_bad_{i}",
                        name="search_chunks",
                        args=json.dumps({}),
                    )
                ]
            )
        ]
        for i in range(5)
    ]
    _install_streams(monkeypatch, loop_harness, events_per_iter)

    def fake_search(*, args, project_id, user_id, ref_offset):
        return {
            "results": [],
            "error": {
                "code": "missing_required_argument",
                "argument": "query",
                "guidance": "...",
            },
        }

    monkeypatch.setattr(chats_module, "execute_search_chunks", fake_search)
    monkeypatch.setattr(
        chats_module,
        "assess_sufficiency",
        lambda *, question, chunks: {
            "sufficient": True,
            "missing": None,
            "feedback": None,
        },
    )

    chat = {"project_id": "proj-1", "id": "chat-1"}
    frames = _drain(
        chats_module._send_message_stream(
            chat=chat,
            text="?",
            chat_id="chat-1",
            user_id="user-1",
            template=None,
        )
    )

    # Should bail at streak == 3, so exactly 3 create calls.
    assert len(loop_harness["create_calls"]) == 3
    deltas = [f["content"] for f in frames if f.get("type") == "delta"]
    assert any("nicht beantwortet werden" in d for d in deltas)
    # Should still emit meta + done at end.
    assert any(f.get("type") == "meta" for f in frames)
    assert frames[-1].get("type") == "done"
