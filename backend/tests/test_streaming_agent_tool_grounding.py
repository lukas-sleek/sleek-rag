"""Unit tests for StreamingAgentTool's grounding-support → [N] marker insertion.

Native vertex_rag_store retrieval doesn't make Gemini emit `[N]` markers
inline (no chunk index handle); Vertex instead returns `grounding_supports`
that tell us which answer segment was grounded on which chunk. We splice
in the markers ourselves so the chat UI's existing [N] flow still works.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.adk.streaming_agent_tool import StreamingAgentTool


def _gm(*, supports, chunks=None):
    """Minimal duck-typed GroundingMetadata: just what _annotate reads."""
    return SimpleNamespace(
        grounding_supports=supports,
        grounding_chunks=chunks or [],
    )


def _support(*, end_index, chunk_indices, part_index=0, start_index=0, text=""):
    return SimpleNamespace(
        segment=SimpleNamespace(
            start_index=start_index,
            end_index=end_index,
            part_index=part_index,
            text=text,
        ),
        grounding_chunk_indices=list(chunk_indices),
        confidence_scores=[1.0] * len(chunk_indices),
    )


def test_annotate_inserts_marker_at_segment_end():
    text = "Thomas Kieliger ist Projektleiter."
    # Segment ends at byte 15 (right after "Thomas Kieliger") → marker [1]
    annotated = StreamingAgentTool._annotate_with_grounding_supports(
        text,
        _gm(supports=[_support(end_index=15, chunk_indices=[0])]),
        idx_offset=0,
    )
    assert annotated == "Thomas Kieliger[1] ist Projektleiter."


def test_annotate_handles_multiple_supports_descending():
    """Two supports with non-overlapping segments → both markers inserted
    without later edits shifting earlier offsets."""
    text = "A ist X. B ist Y."
    annotated = StreamingAgentTool._annotate_with_grounding_supports(
        text,
        _gm(supports=[
            _support(end_index=8, chunk_indices=[0]),    # after "A ist X."
            _support(end_index=17, chunk_indices=[1]),   # after "B ist Y."
        ]),
        idx_offset=0,
    )
    assert annotated == "A ist X.[1] B ist Y.[2]"


def test_annotate_uses_idx_offset_for_global_numbering():
    """idx_offset is the count of chunks already accumulated from previous
    rag_specialist calls in the same turn, so the markers we insert use
    GLOBAL idx values (matching `_citations_from_grounding`'s 1-based
    numbering across the whole turn's chunk list)."""
    text = "Bauherr ist Hochdorf."
    annotated = StreamingAgentTool._annotate_with_grounding_supports(
        text,
        _gm(supports=[_support(end_index=21, chunk_indices=[0, 1])]),
        idx_offset=5,  # five chunks already from a previous rag_specialist call
    )
    assert annotated == "Bauherr ist Hochdorf.[6][7]"


def test_annotate_skips_supports_for_other_parts():
    """Multi-part responses can carry segments tied to a non-text part;
    we only annotate the text part (index 0 / None)."""
    text = "Ein Satz."
    annotated = StreamingAgentTool._annotate_with_grounding_supports(
        text,
        _gm(supports=[
            _support(end_index=9, chunk_indices=[0], part_index=1),  # ignored
            _support(end_index=4, chunk_indices=[0], part_index=0),  # used
        ]),
        idx_offset=0,
    )
    assert annotated == "Ein [1]Satz."


def test_annotate_no_supports_returns_text_unchanged():
    text = "Keine Belege hier."
    annotated = StreamingAgentTool._annotate_with_grounding_supports(
        text, _gm(supports=[]), idx_offset=0,
    )
    assert annotated == text


def test_annotate_handles_utf8_byte_offsets():
    """start_index/end_index are byte offsets per the Vertex spec, not
    character indices. A multi-byte UTF-8 character before the segment
    must not throw off the splice position."""
    # "Ä" = 2 bytes (C3 84) in UTF-8. So "Äbc" is 4 bytes total.
    # Inserting at byte index 4 should land right after "c".
    text = "Äbc def."
    annotated = StreamingAgentTool._annotate_with_grounding_supports(
        text,
        _gm(supports=[_support(end_index=4, chunk_indices=[0])]),
        idx_offset=0,
    )
    assert annotated == "Äbc[1] def."
