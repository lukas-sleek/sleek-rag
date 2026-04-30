"""Factory binding tests (post grounding-migration).

The retrieval tool is now `VertexAiRagRetrieval` — a managed tool that
registers a server-side `Tool(retrieval=...)` on the LLM request rather
than exposing a callable .func. Tests verify the corpus is bound onto
each factory's tool, not the runtime call (covered by the spike probe).
"""
from __future__ import annotations

from app.adk.agents import (
    make_chat_orchestrator,
    make_document_retriever,
    make_rag_specialist,
)


def test_document_retriever_binds_corpus():
    agent = make_document_retriever("projects/p/locations/l/ragCorpora/c1")
    assert agent.name == "document_retriever"
    assert len(agent.tools) == 1
    tool = agent.tools[0]
    assert tool.name == "retrieve_project_documents"
    rag_resources = tool.vertex_rag_store.rag_resources
    assert [r.rag_corpus for r in rag_resources] == [
        "projects/p/locations/l/ragCorpora/c1"
    ]
    # after_model_callback wires our grounding extractor.
    assert agent.after_model_callback is not None


def test_two_factories_isolate_corpora():
    a1 = make_document_retriever("c1")
    a2 = make_document_retriever("c2")
    rs1 = a1.tools[0].vertex_rag_store.rag_resources
    rs2 = a2.tools[0].vertex_rag_store.rag_resources
    assert [r.rag_corpus for r in rs1] == ["c1"]
    assert [r.rag_corpus for r in rs2] == ["c2"]


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
