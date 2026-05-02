"""Factory binding tests — managed retrieval (VertexAiRagRetrieval) edition."""
from __future__ import annotations

from google.adk.tools.retrieval.vertex_ai_rag_retrieval import VertexAiRagRetrieval

from app.adk.agents import make_chat_orchestrator, make_rag_specialist


def test_rag_specialist_uses_vertex_ai_rag_retrieval():
    """rag_specialist must wire ADK's `VertexAiRagRetrieval` directly — that
    primitive injects the native `Tool(retrieval=Retrieval(vertex_rag_store
    =...))` into Gemini's GenerateContent config for Gemini 2+ models, which
    is what enables the multi-step think → retrieve → think loop the agent
    builder demonstrates. Custom FunctionTool retrieval forced one round-
    trip per inference and capped thinking iterations at two."""
    spec = make_rag_specialist("projects/p/locations/us-central1/ragCorpora/c1")
    assert spec.name == "rag_specialist"
    assert spec.model == "gemini-2.5-flash"
    assert len(spec.tools) == 1
    tool = spec.tools[0]
    assert isinstance(tool, VertexAiRagRetrieval)
    assert tool.name == "search_project_documents"
    # Corpus binding is opaque on the tool object; the SDK wraps it in a
    # VertexRagStore alongside other config. We just assert the resource
    # name made it into the store.
    rag_resources = tool.vertex_rag_store.rag_resources or []
    assert len(rag_resources) == 1
    assert (
        rag_resources[0].rag_corpus
        == "projects/p/locations/us-central1/ragCorpora/c1"
    )


def test_two_specialists_isolate_corpora():
    s1 = make_rag_specialist("c1")
    s2 = make_rag_specialist("c2")
    assert s1.tools[0].vertex_rag_store.rag_resources[0].rag_corpus == "c1"
    assert s2.tools[0].vertex_rag_store.rag_resources[0].rag_corpus == "c2"


def test_chat_orchestrator_uses_flash_and_three_tools():
    orch = make_chat_orchestrator("c1")
    assert orch.name == "chat_orchestrator"
    assert orch.model == "gemini-2.5-flash"
    tool_names = [t.name for t in orch.tools]
    assert tool_names == ["rag_specialist", "web_researcher", "run_projektanalyse_v2"]


def test_orchestrator_propagates_grounding_metadata_for_rag_specialist():
    """For citations from the native vertex_rag_store retrieval to reach
    the chat UI, the rag_specialist's wrapping StreamingAgentTool must
    have propagate_grounding_metadata=True. Without it, ADK's AgentTool
    drops the metadata after the sub-agent returns."""
    orch = make_chat_orchestrator("c1")
    rag_tool = next(t for t in orch.tools if t.name == "rag_specialist")
    assert rag_tool.propagate_grounding_metadata is True
    web_tool = next(t for t in orch.tools if t.name == "web_researcher")
    # web_researcher uses its own URL-citation block, no grounding metadata.
    assert web_tool.propagate_grounding_metadata is False
