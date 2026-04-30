"""Plan 19.0 T3-T7 factory binding tests (post document_retriever collapse)."""
from __future__ import annotations

import pytest

from app.adk import retrieval_tool
from app.adk.agents import make_chat_orchestrator, make_rag_specialist


class _ToolCtx:
    def __init__(self):
        self.state = {}


@pytest.mark.asyncio
async def test_rag_specialist_binds_corpus(monkeypatch):
    """rag_specialist owns search_project_documents directly (no
    document_retriever layer). The corpus binding flows through the
    factory closure."""
    seen = []

    def fake_retrieve(*, text, rag_resources, rag_retrieval_config):
        seen.append(rag_resources[0].rag_corpus)
        from types import SimpleNamespace
        return SimpleNamespace(contexts=SimpleNamespace(contexts=[]))

    monkeypatch.setattr(retrieval_tool.rag, "retrieval_query", fake_retrieve)

    spec = make_rag_specialist("projects/p/locations/l/ragCorpora/c1")
    assert spec.name == "rag_specialist"
    assert len(spec.tools) == 1
    tool = spec.tools[0]
    assert tool.name == "search_project_documents"
    await tool.func(query="x", tool_context=_ToolCtx())
    assert seen == ["projects/p/locations/l/ragCorpora/c1"]


@pytest.mark.asyncio
async def test_two_specialists_isolate_corpora(monkeypatch):
    seen = []

    def fake_retrieve(*, text, rag_resources, rag_retrieval_config):
        seen.append(rag_resources[0].rag_corpus)
        from types import SimpleNamespace
        return SimpleNamespace(contexts=SimpleNamespace(contexts=[]))

    monkeypatch.setattr(retrieval_tool.rag, "retrieval_query", fake_retrieve)

    s1 = make_rag_specialist("c1")
    s2 = make_rag_specialist("c2")
    await s1.tools[0].func(query="x", tool_context=_ToolCtx())
    await s2.tools[0].func(query="x", tool_context=_ToolCtx())
    assert seen == ["c1", "c2"]


def test_rag_specialist_uses_flash():
    spec = make_rag_specialist("c1")
    assert spec.name == "rag_specialist"
    assert spec.model == "gemini-2.5-flash"
    assert len(spec.tools) == 1


def test_rag_specialist_has_retry_config():
    """Every LlmAgent in the tree should carry the shared retry config so
    transient DSQ 429s don't fail a chat turn."""
    spec = make_rag_specialist("c1")
    cfg = spec.generate_content_config
    assert cfg is not None
    retry = cfg.http_options.retry_options
    assert retry.attempts and retry.attempts >= 3
    assert 429 in (retry.http_status_codes or [])


def test_chat_orchestrator_uses_flash_and_three_tools():
    orch = make_chat_orchestrator("c1")
    assert orch.name == "chat_orchestrator"
    assert orch.model == "gemini-2.5-flash"
    tool_names = [t.name for t in orch.tools]
    assert tool_names == ["rag_specialist", "web_researcher", "run_projektanalyse_v2"]
