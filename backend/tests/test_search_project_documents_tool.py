"""Plan 20.0: search_project_documents FunctionTool against serverless corpora.

Verifies citation record shape (no page_start/page_end/figure_label),
chunk.file_id -> display_name lookup via rag.list_files, and per-turn
citation accumulation. Mocks rag.retrieval_query + rag.list_files.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.adk import retrieval_tool
from app.adk.retrieval_tool import make_search_project_documents_tool


class _ToolCtx:
    def __init__(self):
        self.state = {}


def _chunk(file_id: str, chunk_id: str = "c1"):
    return SimpleNamespace(file_id=file_id, chunk_id=chunk_id)


def _ctx_from_text(text: str, *, file_id: str = "1001", chunk_id: str = "c1",
                   score: float = 0.5):
    return SimpleNamespace(
        text=text,
        chunk=_chunk(file_id, chunk_id),
        source_uri="gs://internal/temp/" + file_id,
        source_display_name="",
        score=score,
    )


def _make_response(*ctxs):
    return SimpleNamespace(contexts=SimpleNamespace(contexts=list(ctxs)))


def _fake_rag_file(numeric_id: str, display: str):
    return SimpleNamespace(
        name=f"projects/p/locations/us-central1/ragCorpora/c1/ragFiles/{numeric_id}",
        display_name=display,
    )


@pytest.fixture(autouse=True)
def _stub_init_vertex(monkeypatch):
    monkeypatch.setattr(retrieval_tool, "_init_vertex_for", lambda *_a, **_k: "us-central1")
    # Wipe the module-level filename cache between tests.
    retrieval_tool._filename_cache.clear()


@pytest.mark.asyncio
async def test_three_chunks_yield_idx_1_2_3(monkeypatch):
    captured = {}

    def fake_retrieve(*, text, rag_resources, rag_retrieval_config):
        captured["corpus"] = rag_resources[0].rag_corpus
        return _make_response(
            _ctx_from_text("body one", file_id="1001"),
            _ctx_from_text("body two", file_id="1002"),
            _ctx_from_text("body three", file_id="1003"),
        )

    monkeypatch.setattr(retrieval_tool.rag, "retrieval_query", fake_retrieve)
    monkeypatch.setattr(retrieval_tool.rag, "list_files", MagicMock(return_value=[
        _fake_rag_file("1001", "alpha.pdf"),
        _fake_rag_file("1002", "bravo.pdf"),
        _fake_rag_file("1003", "charlie.pdf"),
    ]))

    tool = make_search_project_documents_tool("projects/p/locations/us-central1/ragCorpora/c1")
    ctx = _ToolCtx()
    out = await tool.func(query="x", tool_context=ctx)

    assert out["status"] == "ok"
    assert [c["idx"] for c in out["chunks"]] == [1, 2, 3]
    assert [c["filename"] for c in out["chunks"]] == ["alpha.pdf", "bravo.pdf", "charlie.pdf"]
    assert len(ctx.state["citations"]) == 3
    record = ctx.state["citations"][0]
    assert record["filename"] == "alpha.pdf"
    assert record["snippet"] == "body one"
    assert record["file_id"] == "1001"
    assert record["chunk_id"] == "c1"
    # Page fields gone — record must NOT carry them.
    assert "page_start" not in record
    assert "page_end" not in record
    assert "figure_label" not in record
    assert captured["corpus"] == "projects/p/locations/us-central1/ragCorpora/c1"


@pytest.mark.asyncio
async def test_unknown_file_id_falls_back_to_force_refresh(monkeypatch):
    """First list_files call lacks the chunk's file_id → force refresh kicks in."""
    calls = {"n": 0}

    def fake_list_files(_corpus):
        calls["n"] += 1
        if calls["n"] == 1:
            return []
        return [_fake_rag_file("9999", "delta.pdf")]

    def fake_retrieve(**_kw):
        return _make_response(_ctx_from_text("body", file_id="9999"))

    monkeypatch.setattr(retrieval_tool.rag, "retrieval_query", fake_retrieve)
    monkeypatch.setattr(retrieval_tool.rag, "list_files", fake_list_files)

    tool = make_search_project_documents_tool("c1")
    ctx = _ToolCtx()
    out = await tool.func(query="x", tool_context=ctx)
    assert out["chunks"][0]["filename"] == "delta.pdf"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_empty_contexts_returns_no_results(monkeypatch):
    monkeypatch.setattr(retrieval_tool.rag, "retrieval_query", lambda **_: _make_response())
    monkeypatch.setattr(retrieval_tool.rag, "list_files", lambda _c: [])
    tool = make_search_project_documents_tool("c")
    ctx = _ToolCtx()
    out = await tool.func(query="x", tool_context=ctx)
    assert out == {"status": "no_results", "chunks": []}
    assert ctx.state.get("citations", []) == []


@pytest.mark.asyncio
async def test_second_call_appends_to_existing_citations(monkeypatch):
    def fake_retrieve(**_kw):
        return _make_response(
            _ctx_from_text("a", file_id="1", chunk_id="ca"),
            _ctx_from_text("b", file_id="2", chunk_id="cb"),
            _ctx_from_text("c", file_id="3", chunk_id="cc"),
        )

    monkeypatch.setattr(retrieval_tool.rag, "retrieval_query", fake_retrieve)
    monkeypatch.setattr(retrieval_tool.rag, "list_files", lambda _c: [
        _fake_rag_file("1", "a.pdf"),
        _fake_rag_file("2", "b.pdf"),
        _fake_rag_file("3", "c.pdf"),
    ])
    tool = make_search_project_documents_tool("c")
    ctx = _ToolCtx()
    await tool.func(query="x1", tool_context=ctx)
    out = await tool.func(query="x2", tool_context=ctx)
    assert [c["idx"] for c in out["chunks"]] == [4, 5, 6]
    assert len(ctx.state["citations"]) == 6


@pytest.mark.asyncio
async def test_two_factories_isolate_corpora(monkeypatch):
    seen = []

    def fake_retrieve(*, text, rag_resources, rag_retrieval_config):
        seen.append(rag_resources[0].rag_corpus)
        return _make_response()

    monkeypatch.setattr(retrieval_tool.rag, "retrieval_query", fake_retrieve)
    monkeypatch.setattr(retrieval_tool.rag, "list_files", lambda _c: [])
    t1 = make_search_project_documents_tool("c1")
    t2 = make_search_project_documents_tool("c2")
    await t1.func(query="x", tool_context=_ToolCtx())
    await t2.func(query="x", tool_context=_ToolCtx())
    assert seen == ["c1", "c2"]
