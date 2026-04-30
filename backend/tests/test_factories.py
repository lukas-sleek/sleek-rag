"""Plan 19.0 T3-T7 factory binding tests."""
from __future__ import annotations

import pytest

from app.adk import retrieval_tool
from app.adk.agents import (
    make_chat_orchestrator,
    make_document_retriever,
    make_rag_specialist,
)


class _ToolCtx:
    def __init__(self):
        self.state = {}


@pytest.mark.asyncio
async def test_document_retriever_binds_corpus(monkeypatch):
    seen = []

    def fake_retrieve(*, text, rag_resources, rag_retrieval_config):
        seen.append(rag_resources[0].rag_corpus)
        from types import SimpleNamespace
        return SimpleNamespace(contexts=SimpleNamespace(contexts=[]))

    monkeypatch.setattr(retrieval_tool.rag, "retrieval_query", fake_retrieve)

    agent = make_document_retriever("projects/p/locations/l/ragCorpora/c1")
    assert agent.name == "document_retriever"
    assert len(agent.tools) == 1
    tool = agent.tools[0]
    await tool.func(query="x", tool_context=_ToolCtx())
    assert seen == ["projects/p/locations/l/ragCorpora/c1"]


@pytest.mark.asyncio
async def test_two_factories_isolate_corpora(monkeypatch):
    seen = []

    def fake_retrieve(*, text, rag_resources, rag_retrieval_config):
        seen.append(rag_resources[0].rag_corpus)
        from types import SimpleNamespace
        return SimpleNamespace(contexts=SimpleNamespace(contexts=[]))

    monkeypatch.setattr(retrieval_tool.rag, "retrieval_query", fake_retrieve)

    a1 = make_document_retriever("c1")
    a2 = make_document_retriever("c2")
    await a1.tools[0].func(query="x", tool_context=_ToolCtx())
    await a2.tools[0].func(query="x", tool_context=_ToolCtx())
    assert seen == ["c1", "c2"]


def test_rag_specialist_uses_flash():
    spec = make_rag_specialist("c1")
    assert spec.name == "rag_specialist"
    assert spec.model == "gemini-2.5-flash"
    assert len(spec.tools) == 1


def test_chat_orchestrator_uses_flash_and_three_tools():
    orch = make_chat_orchestrator("c1")
    assert orch.name == "chat_orchestrator"
    assert orch.model == "gemini-2.5-flash"
    tool_names = [t.name for t in orch.tools]
    assert tool_names == ["rag_specialist", "web_researcher", "run_projektanalyse_v2"]
