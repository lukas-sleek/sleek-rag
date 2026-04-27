"""Unit tests for prompt assembly. Mocks Supabase Storage download for image
attachment paths so this test suite stays offline."""
from unittest.mock import patch

import pytest

from app.prompt import MAX_IMAGES_PER_TURN, build_messages
from app.retrieval import RetrievedChunk


def _chunk(idx: int, *, image_path: str | None = None, figure_label: str | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"chunk-{idx}",
        file_id="file-1",
        filename="doc.pdf",
        project_id="proj-1",
        content=f"chunk content {idx}",
        page_start=idx,
        page_end=idx,
        figure_label=figure_label,
        block_type="figure" if image_path else "paragraph",
        score=0.5,
        image_path=image_path,
    )


def test_text_only_when_no_images():
    msgs = build_messages(
        query="What is X?", history=[], chunks=[_chunk(1), _chunk(2)]
    )
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"
    content = msgs[-1]["content"]
    assert isinstance(content, str)
    assert "[1] doc.pdf p.1" in content
    assert "Question: What is X?" in content


def test_history_passed_through():
    history = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "earlier reply"},
    ]
    msgs = build_messages(query="next", history=history, chunks=[])
    assert msgs[1] == history[0]
    assert msgs[2] == history[1]


def test_no_chunks_yields_no_context_marker():
    msgs = build_messages(query="hi", history=[], chunks=[])
    assert "(No context retrieved.)" in msgs[-1]["content"]


def test_image_parts_attach_when_path_present():
    chunks = [
        _chunk(1, image_path="u/f/c1.png", figure_label="Figure 1"),
        _chunk(2),  # no image
    ]
    fake_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

    with patch("app.prompt.supabase") as sb:
        sb.return_value.storage.from_.return_value.download.return_value = fake_bytes
        msgs = build_messages(query="what is in the figure?", history=[], chunks=chunks)

    content = msgs[-1]["content"]
    assert isinstance(content, list)
    text_part = next(p for p in content if p["type"] == "text")
    image_parts = [p for p in content if p["type"] == "image_url"]
    assert "Figure 1" in text_part["text"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_image_cap_enforced():
    chunks = [_chunk(i, image_path=f"u/f/c{i}.png") for i in range(5)]
    fake_bytes = b"\x89PNG" + b"\x00" * 64

    with patch("app.prompt.supabase") as sb:
        sb.return_value.storage.from_.return_value.download.return_value = fake_bytes
        msgs = build_messages(query="figs?", history=[], chunks=chunks)

    image_parts = [p for p in msgs[-1]["content"] if p["type"] == "image_url"]
    assert len(image_parts) == MAX_IMAGES_PER_TURN


def test_image_skipped_when_oversized():
    chunks = [_chunk(1, image_path="u/f/c1.png")]
    huge = b"\x00" * (5 * 1024 * 1024)

    with patch("app.prompt.supabase") as sb:
        sb.return_value.storage.from_.return_value.download.return_value = huge
        msgs = build_messages(query="x", history=[], chunks=chunks)

    # Falls back to text-only because the only candidate image was too large.
    assert isinstance(msgs[-1]["content"], str)


def test_image_skipped_when_download_fails():
    chunks = [_chunk(1, image_path="u/f/c1.png")]

    with patch("app.prompt.supabase") as sb:
        sb.return_value.storage.from_.return_value.download.side_effect = RuntimeError("nope")
        msgs = build_messages(query="x", history=[], chunks=chunks)

    assert isinstance(msgs[-1]["content"], str)
