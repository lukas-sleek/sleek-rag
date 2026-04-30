"""Plan 19.0 T2 unit tests for the search_project_documents FunctionTool factory.

Mocks `vertexai.preview.rag.async_retrieve_contexts` so we can exercise the
chunk-shaping, [Seite N] / [Abb. N: …] regex enrichment, and per-turn
citation accumulation without hitting Vertex.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.adk import retrieval_tool
from app.adk.retrieval_tool import make_search_project_documents_tool


class _ToolCtx:
    """Minimal stand-in for ADK ToolContext — only `state` is used."""

    def __init__(self):
        self.state = {}


def _ctx_from_text(text: str, *, source_uri: str = "gs://b/f.pdf",
                    source_display_name: str | None = "f.pdf",
                    score: float | None = 0.5):
    return SimpleNamespace(
        text=text,
        source_uri=source_uri,
        source_display_name=source_display_name,
        score=score,
    )


def _make_response(*ctxs):
    return SimpleNamespace(
        contexts=SimpleNamespace(contexts=list(ctxs))
    )


@pytest.mark.asyncio
async def test_three_chunks_yield_idx_1_2_3(monkeypatch):
    captured = {}

    def fake_retrieve(*, text, rag_resources, rag_retrieval_config):
        captured["corpus"] = rag_resources[0].rag_corpus
        return _make_response(
            _ctx_from_text("[Seite 1] body one"),
            _ctx_from_text("[Seite 2] body two"),
            _ctx_from_text("[Seite 3] body three"),
        )

    monkeypatch.setattr(retrieval_tool.rag, "retrieval_query", fake_retrieve)

    tool = make_search_project_documents_tool("projects/p/locations/l/ragCorpora/c1")
    ctx = _ToolCtx()
    out = await tool.func(query="x", tool_context=ctx)

    assert out["status"] == "ok"
    assert [c["idx"] for c in out["chunks"]] == [1, 2, 3]
    assert len(ctx.state["citations"]) == 3
    assert captured["corpus"] == "projects/p/locations/l/ragCorpora/c1"


@pytest.mark.asyncio
async def test_single_page_marker(monkeypatch):
    def fake_retrieve(**_kw):
        return _make_response(_ctx_from_text("[Seite 14] some text"))

    monkeypatch.setattr(retrieval_tool.rag, "retrieval_query", fake_retrieve)
    tool = make_search_project_documents_tool("c")
    ctx = _ToolCtx()
    out = await tool.func(query="x", tool_context=ctx)
    chunk = out["chunks"][0]
    assert chunk["page_start"] == 14
    assert chunk["page_end"] == 14


@pytest.mark.asyncio
async def test_page_range(monkeypatch):
    def fake_retrieve(**_kw):
        return _make_response(_ctx_from_text("[Seite 14] foo [Seite 15] bar"))

    monkeypatch.setattr(retrieval_tool.rag, "retrieval_query", fake_retrieve)
    tool = make_search_project_documents_tool("c")
    ctx = _ToolCtx()
    out = await tool.func(query="x", tool_context=ctx)
    chunk = out["chunks"][0]
    assert chunk["page_start"] == 14
    assert chunk["page_end"] == 15


@pytest.mark.asyncio
async def test_figure_label_recorded(monkeypatch):
    def fake_retrieve(**_kw):
        return _make_response(_ctx_from_text("[Abb. 3.2: Lageplan] caption text"))

    monkeypatch.setattr(retrieval_tool.rag, "retrieval_query", fake_retrieve)
    tool = make_search_project_documents_tool("c")
    ctx = _ToolCtx()
    await tool.func(query="x", tool_context=ctx)
    assert ctx.state["citations"][0]["figure_label"] == "Abb. 3.2"


@pytest.mark.asyncio
async def test_no_page_marker_yields_null_pages(monkeypatch):
    def fake_retrieve(**_kw):
        return _make_response(_ctx_from_text("plain text without markers"))

    monkeypatch.setattr(retrieval_tool.rag, "retrieval_query", fake_retrieve)
    tool = make_search_project_documents_tool("c")
    ctx = _ToolCtx()
    out = await tool.func(query="x", tool_context=ctx)
    chunk = out["chunks"][0]
    assert chunk["page_start"] is None
    assert chunk["page_end"] is None


@pytest.mark.asyncio
async def test_empty_contexts_returns_no_results(monkeypatch):
    def fake_retrieve(**_kw):
        return _make_response()

    monkeypatch.setattr(retrieval_tool.rag, "retrieval_query", fake_retrieve)
    tool = make_search_project_documents_tool("c")
    ctx = _ToolCtx()
    out = await tool.func(query="x", tool_context=ctx)
    assert out == {"status": "no_results", "chunks": []}
    assert ctx.state.get("citations", []) == []


@pytest.mark.asyncio
async def test_second_call_appends_to_existing_citations(monkeypatch):
    def fake_retrieve(**_kw):
        return _make_response(
            _ctx_from_text("[Seite 1] a"),
            _ctx_from_text("[Seite 2] b"),
            _ctx_from_text("[Seite 3] c"),
        )

    monkeypatch.setattr(retrieval_tool.rag, "retrieval_query", fake_retrieve)
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
    t1 = make_search_project_documents_tool("c1")
    t2 = make_search_project_documents_tool("c2")
    await t1.func(query="x", tool_context=_ToolCtx())
    await t2.func(query="x", tool_context=_ToolCtx())
    assert seen == ["c1", "c2"]
