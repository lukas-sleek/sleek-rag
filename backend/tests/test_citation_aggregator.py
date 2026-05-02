"""Plan 19.0 T9c unit tests for citation_aggregator."""
from __future__ import annotations

from app.adk.citation_aggregator import dedupe_and_renumber, rewrite_refs


def _c(idx, file_id, chunk_id, snippet="x"):
    return {
        "idx": idx,
        "kind": "file",
        "file_id": file_id,
        "chunk_id": chunk_id,
        "snippet": snippet,
    }


def test_three_unique_chunks_passthrough():
    raw = [
        _c(1, "f1", "c1"),
        _c(2, "f2", "c2"),
        _c(3, "f3", "c3"),
    ]
    final, remap = dedupe_and_renumber(raw)
    assert [c["idx"] for c in final] == [1, 2, 3]
    assert remap == {1: 1, 2: 2, 3: 3}


def test_two_duplicates_collapse():
    raw = [
        _c(1, "f1", "c1"),
        _c(2, "f1", "c1"),
    ]
    final, remap = dedupe_and_renumber(raw)
    assert len(final) == 1
    assert remap == {1: 1, 2: 1}


def test_different_chunk_ids_same_file_do_not_collapse():
    raw = [
        _c(1, "f1", "c1"),
        _c(2, "f1", "c2"),
    ]
    final, remap = dedupe_and_renumber(raw)
    assert len(final) == 2
    assert remap == {1: 1, 2: 2}


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
        _c(1, "f1", "c1"),
        _c(2, "f2", "c2"),
        _c(3, "f1", "c1"),  # dup of 1
        _c(4, "f3", "c3"),
    ]
    final, remap = dedupe_and_renumber(raw)
    assert [c["idx"] for c in final] == [1, 2, 3]
    assert remap == {1: 1, 2: 2, 3: 1, 4: 3}
