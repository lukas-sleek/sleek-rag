"""Plan 19.0 T9c unit tests for citation_aggregator."""
from __future__ import annotations

from app.adk.citation_aggregator import dedupe_and_renumber, rewrite_refs


def _c(idx, uri, page, snippet):
    return {
        "idx": idx,
        "uri": uri,
        "page_start": page,
        "page_end": page,
        "snippet": snippet,
    }


def test_three_unique_chunks_passthrough():
    raw = [
        _c(1, "gs://a", 1, "alpha"),
        _c(2, "gs://b", 2, "bravo"),
        _c(3, "gs://c", 3, "charlie"),
    ]
    final, remap = dedupe_and_renumber(raw)
    assert [c["idx"] for c in final] == [1, 2, 3]
    assert remap == {1: 1, 2: 2, 3: 3}


def test_two_duplicates_collapse():
    raw = [
        _c(1, "gs://a", 1, "alpha"),
        _c(2, "gs://a", 1, "alpha"),
    ]
    final, remap = dedupe_and_renumber(raw)
    assert len(final) == 1
    assert remap == {1: 1, 2: 1}


def test_dedup_uses_first_80_chars_of_snippet():
    raw = [
        _c(1, "gs://a", 1, "x" * 100),
        _c(2, "gs://a", 1, "x" * 80 + "DIFFERENT"),
    ]
    final, remap = dedupe_and_renumber(raw)
    assert len(final) == 1
    assert remap == {1: 1, 2: 1}


def test_rewrite_refs_basic():
    text = "First[1] then[2] also[3]."
    out = rewrite_refs(text, {1: 1, 2: 1, 3: 2})
    assert out == "First[1] then[1] also[2]."


def test_rewrite_refs_unknown_passthrough():
    text = "foo[7]"
    out = rewrite_refs(text, {1: 1})
    assert out == "foo[7]"


def test_rewrite_refs_preserves_non_marker_text():
    text = "Plain text without markers."
    assert rewrite_refs(text, {1: 1}) == text


def test_renumber_after_collapse_preserves_global_order():
    raw = [
        _c(1, "gs://a", 1, "alpha"),
        _c(2, "gs://b", 2, "bravo"),
        _c(3, "gs://a", 1, "alpha"),  # dup of 1
        _c(4, "gs://c", 3, "charlie"),
    ]
    final, remap = dedupe_and_renumber(raw)
    assert [c["idx"] for c in final] == [1, 2, 3]
    assert remap == {1: 1, 2: 2, 3: 1, 4: 3}
