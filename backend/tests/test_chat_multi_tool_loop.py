"""Unit test for the plan-17 multi-tool agent loop in chats._send_message_stream.

Verifies that across one chat turn the loop:
  1. Dispatches `search_chunks`, `list_document_outline`, and `read_section`
     to their respective executors.
  2. Passes a contiguous `ref_offset` to each call (refs from search +
     read_section are renumbered together).
  3. Produces a final assistant text response after the third tool result.

Mocks the Gemini stream, the supabase persistence calls, and each executor.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.routers import chats as chats_module
from app.retrieval import RetrievedChunk


def _mk_event(*, content: str | None = None, tool_calls=None, finish_reason=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


def _mk_tool_delta(*, idx: int, tc_id: str, name: str, args: str):
    fn = SimpleNamespace(name=name, arguments=args)
    return SimpleNamespace(index=idx, id=tc_id, function=fn)


def _mk_chunk(chunk_id: str) -> RetrievedChunk:
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
def chat_loop_mocks(monkeypatch):
    captured = {
        "executor_calls": [],  # list[(tool_name, args, ref_offset)]
    }

    # Inserted user message + history reads + final assistant insert all use
    # supabase(). MagicMock with a chained .table().insert().execute() returns
    # a SimpleNamespace whose .data attribute can be customized per call.
    def fake_supabase():
        sb = MagicMock()
        chain = MagicMock()

        # Track whether the chain represents an insert (returns a row with
        # an `id`) or a select (returns no history). The loop chains
        # .table().insert().execute() and .table().select().eq()....execute().
        chain._is_insert = False

        def insert_returns(*_a, **_k):
            chain._is_insert = True
            return chain

        def select_returns(*_a, **_k):
            chain._is_insert = False
            return chain

        def execute():
            if chain._is_insert:
                # Reset for the next chain; tests don't care about which
                # specific row id comes back, only that insert returns one.
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

    # Three iterations of tool calls, then a final text iteration.
    def make_stream_for(call_idx: list[int]):
        events_per_iter = [
            # 1. search_chunks
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
            # 2. list_document_outline
            [
                _mk_event(
                    tool_calls=[
                        _mk_tool_delta(
                            idx=0,
                            tc_id="call_outline",
                            name="list_document_outline",
                            args=json.dumps({"file_id": "abcd1234"}),
                        )
                    ]
                ),
            ],
            # 3. read_section
            [
                _mk_event(
                    tool_calls=[
                        _mk_tool_delta(
                            idx=0,
                            tc_id="call_read",
                            name="read_section",
                            args=json.dumps(
                                {
                                    "file_id": "abcd1234",
                                    "section": "Projektorganisation",
                                }
                            ),
                        )
                    ]
                ),
            ],
            # 4. final text
            [
                _mk_event(content="Final answer with [1] and [4]."),
            ],
        ]

        def fake_create(**_kw):
            i = call_idx[0]
            call_idx[0] += 1
            events = events_per_iter[i]
            return iter(events)

        return fake_create

    call_idx = [0]
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=make_stream_for(call_idx))
        )
    )
    monkeypatch.setattr(
        chats_module, "gemini_client_untraced", lambda: fake_client
    )

    # Stub each executor; record the ref_offset they receive.
    def fake_search(*, args, project_id, user_id, ref_offset):
        captured["executor_calls"].append(("search_chunks", args, ref_offset))
        chunks = [_mk_chunk("c1"), _mk_chunk("c2"), _mk_chunk("c3")]
        results = [
            {"ref": ref_offset + i + 1, "chunk_id": c.chunk_id}
            for i, c in enumerate(chunks)
        ]
        return {"results": results, "_chunks": chunks}

    def fake_outline(*, args, project_id, user_id, ref_offset):
        captured["executor_calls"].append(
            ("list_document_outline", args, ref_offset)
        )
        return {"file_id": args["file_id"], "outline": [{"heading": "X"}]}

    def fake_read(*, args, project_id, user_id, ref_offset):
        captured["executor_calls"].append(("read_section", args, ref_offset))
        chunks = [_mk_chunk("c4"), _mk_chunk("c5")]
        results = [
            {"ref": ref_offset + i + 1, "chunk_id": c.chunk_id}
            for i, c in enumerate(chunks)
        ]
        return {"results": results, "_chunks": chunks}

    monkeypatch.setattr(chats_module, "execute_search_chunks", fake_search)
    monkeypatch.setattr(
        chats_module, "list_document_outline_executor", fake_outline
    )
    monkeypatch.setattr(chats_module, "read_section_executor", fake_read)

    return captured


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


def test_loop_dispatches_all_three_tools_with_contiguous_refs(chat_loop_mocks):
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

    calls = chat_loop_mocks["executor_calls"]
    names = [c[0] for c in calls]
    assert names == ["search_chunks", "list_document_outline", "read_section"]

    # ref_offset progression: search starts at 0, outline doesn't allocate
    # refs (returns no `results` field for the agent loop's len(results)
    # accumulator — outline returns a separate "outline" key), read_section
    # starts at 3 because search added three results.
    offsets = [c[2] for c in calls]
    assert offsets == [0, 3, 3]

    # Final text frame and meta citations frame must both appear.
    types = [f.get("type") for f in frames]
    assert "delta" in types
    assert "meta" in types
    assert types[-1] == "done"

    meta = next(f for f in frames if f.get("type") == "meta")
    # search added 3 chunks, read_section added 2 → 5 citations total.
    assert len(meta["citations"]) == 5
