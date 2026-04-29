"""Plan 18.3 T7: grounding_metadata → frontend Citation shape."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import citations


class _Resp:
    def __init__(self, data):
        self.data = data


class _SupabaseStub:
    """Returns a fixed project_files list keyed on gcs_blob_path."""

    def __init__(self, files):
        self._files = files

    def table(self, _name):
        return self

    def select(self, _cols):
        return self

    def eq(self, _k, _v):
        return self

    def execute(self):
        return _Resp(self._files)


def _response_with_chunks(chunks):
    return SimpleNamespace(
        candidates=[SimpleNamespace(grounding_metadata=SimpleNamespace(grounding_chunks=chunks))]
    )


def _chunk(uri: str, text: str, title: str | None = None):
    return SimpleNamespace(
        retrieved_context=SimpleNamespace(uri=uri, text=text, title=title)
    )


def test_returns_empty_when_response_is_none(monkeypatch):
    monkeypatch.setattr(citations, "supabase", lambda: _SupabaseStub([]))
    assert citations.grounding_to_citations_sync(None, "p1") == []


def test_returns_empty_when_no_grounding_metadata(monkeypatch):
    monkeypatch.setattr(citations, "supabase", lambda: _SupabaseStub([]))
    resp = SimpleNamespace(candidates=[SimpleNamespace(grounding_metadata=None)])
    assert citations.grounding_to_citations_sync(resp, "p1") == []


def test_parses_page_marker_and_filename_lookup(monkeypatch):
    uri = "gs://bucket/u/p/abc/original.pdf"
    monkeypatch.setattr(
        citations,
        "supabase",
        lambda: _SupabaseStub(
            [{"id": "f1", "filename": "spec.pdf", "gcs_blob_path": uri}]
        ),
    )
    resp = _response_with_chunks(
        [_chunk(uri, "[Seite 14]\n# Kapitel 1\nDer Inhalt der Seite.")]
    )
    out = citations.grounding_to_citations_sync(resp, "p1")
    assert len(out) == 1
    cit = out[0]
    assert cit["page_start"] == 14
    assert cit["page_end"] == 14
    assert cit["file_id"] == "f1"
    assert cit["filename"] == "spec.pdf"
    assert cit["figure_label"] is None
    assert "[Seite 14]" in cit["snippet"]


def test_first_and_last_page_marker_when_chunk_spans_pages(monkeypatch):
    monkeypatch.setattr(citations, "supabase", lambda: _SupabaseStub([]))
    text = "[Seite 12]\nfoo\n\n[Seite 13]\nbar\n\n[Seite 14]\nbaz"
    resp = _response_with_chunks([_chunk("gs://b/u/p/x/o.pdf", text)])
    out = citations.grounding_to_citations_sync(resp, "p1")
    assert out[0]["page_start"] == 12
    assert out[0]["page_end"] == 14


def test_figure_label_extracted(monkeypatch):
    monkeypatch.setattr(citations, "supabase", lambda: _SupabaseStub([]))
    text = "[Seite 9]\n[Abb. 3.2: Querschnitt der Brücke] Beschreibung."
    resp = _response_with_chunks([_chunk("gs://b/u/p/x/o.pdf", text)])
    out = citations.grounding_to_citations_sync(resp, "p1")
    assert out[0]["figure_label"] == "Abb. 3.2"


def test_unknown_uri_falls_back_to_title_then_uri(monkeypatch):
    monkeypatch.setattr(citations, "supabase", lambda: _SupabaseStub([]))
    resp = _response_with_chunks(
        [
            _chunk("gs://b/u/p/x/o.pdf", "Kein Marker", title="Tender.pdf"),
            _chunk("gs://b/u/p/y/o.pdf", "Kein Marker", title=None),
        ]
    )
    out = citations.grounding_to_citations_sync(resp, "p1")
    assert out[0]["filename"] == "Tender.pdf"
    assert out[0]["file_id"] is None
    assert out[1]["filename"] == "gs://b/u/p/y/o.pdf"


def test_no_page_marker_returns_none_pages(monkeypatch):
    monkeypatch.setattr(citations, "supabase", lambda: _SupabaseStub([]))
    resp = _response_with_chunks(
        [_chunk("gs://b/u/p/x/o.pdf", "Kein Seitenmarker hier.")]
    )
    out = citations.grounding_to_citations_sync(resp, "p1")
    assert out[0]["page_start"] is None
    assert out[0]["page_end"] is None
