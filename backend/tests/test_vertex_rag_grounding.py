"""Plan 18.3 Task 4: grounding-tool helper unit tests."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import vertex_rag_grounding


class _Resp:
    def __init__(self, data):
        self.data = data


class _Stub:
    """Minimal supabase-shaped chain stub that returns a configured row."""

    def __init__(self, data):
        self._data = data

    def table(self, _name):
        return self

    def select(self, _cols):
        return self

    def eq(self, _k, _v):
        return self

    def single(self):
        return self

    def execute(self):
        return _Resp(self._data)


def test_returns_tool_with_corpus_name(monkeypatch):
    corpus = (
        "projects/test-project/locations/europe-west3/ragCorpora/abc123"
    )
    monkeypatch.setattr(
        vertex_rag_grounding,
        "supabase",
        lambda: _Stub({"rag_corpus_name": corpus}),
    )
    tool = vertex_rag_grounding.grounding_tool_for_project("p1")
    assert tool is not None
    rag_store = tool.retrieval.vertex_rag_store
    assert rag_store.rag_resources[0].rag_corpus == corpus


def test_returns_none_when_corpus_missing(monkeypatch):
    monkeypatch.setattr(
        vertex_rag_grounding,
        "supabase",
        lambda: _Stub({"rag_corpus_name": None}),
    )
    assert vertex_rag_grounding.grounding_tool_for_project("p1") is None


def test_returns_none_when_row_missing(monkeypatch):
    monkeypatch.setattr(
        vertex_rag_grounding,
        "supabase",
        lambda: _Stub(None),
    )
    assert vertex_rag_grounding.grounding_tool_for_project("p1") is None
