"""Build the Vertex RAG retrieval grounding tool for a project's corpus.

Plan 18.3 Task 4. Mirrors the JS reference in scripts/benchmark/clients/vanilla.py:
no rag_retrieval_config — defaults match the user's vanilla test verbatim.
"""
from __future__ import annotations

from google.genai import types

from app.db import supabase


def grounding_tool_for_project(project_id: str) -> types.Tool | None:
    """Return the Vertex RAG grounding Tool for this project, or None if the
    project has no corpus yet (file uploads create the corpus lazily — until
    the first upload completes, the chat session has no corpus to ground on)."""
    row = (
        supabase()
        .table("projects")
        .select("rag_corpus_name")
        .eq("id", project_id)
        .single()
        .execute()
    )
    corpus = (row.data or {}).get("rag_corpus_name")
    if not corpus:
        return None
    return types.Tool(
        retrieval=types.Retrieval(
            vertex_rag_store=types.VertexRagStore(
                rag_resources=[
                    types.VertexRagStoreRagResource(rag_corpus=corpus)
                ]
            )
        )
    )
