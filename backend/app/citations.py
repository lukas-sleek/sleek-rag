"""Pattern A citation extraction (plan 18.3 T7).

Maps `grounding_metadata.grounding_chunks[*]` (Vertex RAG retrieval) to the
frontend Citation shape, regex-enriching page numbers and figure labels
from the chunk text. The LLM Parser prompt (plan 18.2 T2) is responsible
for emitting `[Seite N]` and `[Abb. N: ...]` markers in every chunk —
fidelity of this regex enrichment is downstream of parser fidelity.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Any

from app.db import supabase

log = logging.getLogger(__name__)


_PAGE_RE = re.compile(r"\[Seite\s+(\d+)\]")
_FIGURE_RE = re.compile(r"\[Abb\.?\s*(\d+(?:\.\d+)*)\s*:\s*([^\]]+)\]")


def _files_by_gcs_uri_sync(project_id: str) -> dict[str, dict]:
    rows = (
        supabase()
        .table("project_files")
        .select("id, filename, gcs_blob_path")
        .eq("project_id", project_id)
        .execute()
    )
    out: dict[str, dict] = {}
    for r in rows.data or []:
        path = r.get("gcs_blob_path")
        if path:
            out[path] = r
    return out


def _extract_text(retrieved_context: Any) -> str:
    """`retrieved_context.text` carries the chunk body. The Vertex SDK
    occasionally returns it under `.content` instead — try both."""
    text = getattr(retrieved_context, "text", None)
    if text:
        return text
    return getattr(retrieved_context, "content", "") or ""


def grounding_to_citations_sync(response: Any, project_id: str) -> list[dict]:
    """Synchronous citation extractor. Use the async wrapper from request
    handlers; this entrypoint is kept for tests + tooling."""
    if response is None:
        return []
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return []
    meta = getattr(candidates[0], "grounding_metadata", None)
    if meta is None:
        return []
    chunks = getattr(meta, "grounding_chunks", None) or []
    if not chunks:
        return []

    files_by_uri = _files_by_gcs_uri_sync(project_id)

    citations: list[dict] = []
    for chunk in chunks:
        ctx = getattr(chunk, "retrieved_context", None)
        if ctx is None:
            continue
        uri = getattr(ctx, "uri", None) or ""
        text = _extract_text(ctx)
        page_starts = [int(m.group(1)) for m in _PAGE_RE.finditer(text)]
        figure_match = _FIGURE_RE.search(text)
        file_row = files_by_uri.get(uri, {})

        snippet = (text or "").strip()[:200]
        chunk_id = hashlib.sha1(
            f"{uri}|{snippet}".encode("utf-8")
        ).hexdigest()[:16]

        citations.append(
            {
                "chunk_id": chunk_id,
                "file_id": file_row.get("id"),
                "filename": (
                    file_row.get("filename")
                    or getattr(ctx, "title", None)
                    or uri
                ),
                "page_start": page_starts[0] if page_starts else None,
                "page_end": page_starts[-1] if page_starts else None,
                "snippet": snippet,
                "figure_label": (
                    f"Abb. {figure_match.group(1)}" if figure_match else None
                ),
                "image_path": None,
                "score": None,
            }
        )
    return citations


async def grounding_to_citations(response: Any, project_id: str) -> list[dict]:
    """Async wrapper — supabase client calls are sync, dispatch to a thread."""
    return await asyncio.to_thread(
        grounding_to_citations_sync, response, project_id
    )


def _supports_to_char_offsets(
    response: Any, answer_text: str
) -> list[tuple[int, list[int]]]:
    """Returns [(end_char, chunk_indices), ...] sorted by end_char asc.

    Vertex emits `grounding_metadata.grounding_supports`: each entry pins a
    span of the answer (UTF-8 byte offsets into the response text) to the
    indices of the supporting chunks in `grounding_chunks`. We convert those
    byte offsets to Python char offsets so the splicer below can work on a
    plain `str` without re-encoding.
    """
    if response is None:
        return []
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return []
    meta = getattr(candidates[0], "grounding_metadata", None)
    if meta is None:
        return []
    supports = getattr(meta, "grounding_supports", None) or []
    if not supports:
        return []

    answer_bytes = answer_text.encode("utf-8")
    out: list[tuple[int, list[int]]] = []
    for sup in supports:
        seg = getattr(sup, "segment", None)
        if seg is None:
            continue
        # Vertex puts indices on UTF-8 bytes. Skip non-zero part_index — for
        # plain-text answers it's always 0; if Vertex ever splits into
        # multiple parts we'd need part-aware offset bookkeeping.
        if (getattr(seg, "part_index", 0) or 0) != 0:
            continue
        start_b = getattr(seg, "start_index", None)
        end_b = getattr(seg, "end_index", None)
        if start_b is None or end_b is None:
            continue
        if start_b < 0 or end_b > len(answer_bytes) or start_b >= end_b:
            continue
        try:
            end_char = len(answer_bytes[:end_b].decode("utf-8"))
        except UnicodeDecodeError:
            # Vertex *should* respect UTF-8 boundaries; if not, we'd corrupt
            # the answer by splicing mid-codepoint, so skip.
            continue
        chunk_indices = list(getattr(sup, "grounding_chunk_indices", None) or [])
        if not chunk_indices:
            continue
        out.append((end_char, chunk_indices))
    out.sort(key=lambda s: s[0])
    return out


def annotate_answer_with_refs(
    response: Any, answer_text: str
) -> str:
    """Splice `[N]` ref markers into `answer_text` at the end of each
    grounded span. `N = chunk_index + 1` (the existing chat.tsx
    `linkifyCitations` regex matches `\\[\\d+\\]` and re-numbers per
    first-appearance + chunk_id dedupe, so emitting ref-by-chunk-index
    is enough — the frontend picks the renumbering).

    Returns `answer_text` unchanged when no grounding_supports are present.
    """
    supports = _supports_to_char_offsets(response, answer_text)
    if not supports:
        return answer_text
    # Splice from end → start so earlier offsets remain valid.
    out = answer_text
    for end_char, chunk_indices in sorted(supports, key=lambda s: s[0], reverse=True):
        markers = "".join(f"[{i + 1}]" for i in chunk_indices)
        out = out[:end_char] + markers + out[end_char:]
    return out
