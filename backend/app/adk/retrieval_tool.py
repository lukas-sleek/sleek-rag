"""Managed Vertex RAG retrieval for the chat agent (post-grounding migration).

Replaces the prior FunctionTool wrapper around `rag.retrieval_query`. We now
use ADK's built-in `VertexAiRagRetrieval` which, on Gemini 2.x, registers a
server-side `Tool(retrieval=Retrieval(vertex_rag_store=...))` on the model
config — the same managed retrieval pattern Agent Builder uses.

The model itself rewrites the query, retrieves chunks, and grounds its
answer; the result lands as `grounding_metadata` on the LlmResponse. We
extract two things from that metadata via an `after_model_callback`:

  1. **Citation records** — one per `grounding_chunk`, written to
     `state["citations"]`. Page numbers come from
     `rag_chunk.page_span.{first_page,last_page}` (structured field), not
     a regex over chunk text. Full chunk text + per-chunk confidence (max
     across supports that cite it) are stored too.

  2. **Inline `[N]` markers** — spliced into the model's response text at
     `grounding_supports[].segment.end_index`, so the existing citation
     pipeline (rag_specialist preserves [N], chat_orchestrator forwards
     them, citation_aggregator dedupes + renumbers) keeps working without
     changes upstream.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.retrieval.vertex_ai_rag_retrieval import VertexAiRagRetrieval
from vertexai.preview import rag

log = logging.getLogger(__name__)

# GCS layout per gcs.py: gs://{bucket}/{user_id}/{project_id}/{file_id}/{name}.pdf
# project_id is the 3rd slash-separated segment, file_id the 4th.
_GCS_URI_RE = re.compile(
    r"^gs://[^/]+/[^/]+/(?P<project_id>[0-9a-fA-F-]{36})/(?P<file_id>[0-9a-fA-F-]{36})/"
)

_DEFAULT_TOP_K = 10


def _ids_from_uri(uri: str | None) -> tuple[str | None, str | None]:
    """Return (project_id, file_id) parsed from the canonical GCS URI shape,
    or (None, None) if the URI doesn't match (legacy rows / non-GCS paths).
    """
    if not uri:
        return None, None
    m = _GCS_URI_RE.match(uri)
    if not m:
        return None, None
    return m.group("project_id"), m.group("file_id")


def make_rag_tool(
    corpus_name: str, *, top_k: int = _DEFAULT_TOP_K
) -> VertexAiRagRetrieval:
    """Build the managed retrieval tool for one project's corpus.

    On Gemini 2.x, `VertexAiRagRetrieval.process_llm_request` registers a
    server-side `Tool(retrieval=Retrieval(vertex_rag_store=...))` on the
    LlmRequest's config — same path Agent Builder takes. The agent's LLM
    issues retrieval, grounds the answer, and the result comes back via
    the callback below.
    """
    return VertexAiRagRetrieval(
        name="retrieve_project_documents",
        description=(
            "Sucht relevante Passagen im Projektkorpus dieses Projekts. "
            "Liefert eine fundierte Antwort mit Grounding-Quellen "
            "(Datei, Seite, Konfidenz) zurueck. Wird ausschliesslich "
            "vom rag_specialist als Werkzeug aufgerufen."
        ),
        rag_resources=[rag.RagResource(rag_corpus=corpus_name)],
        similarity_top_k=top_k,
    )


def _extract_grounded_text(content: Any) -> tuple[str, list[Any]] | None:
    """Concatenate all text parts from an LlmResponse.content, returning
    `(joined_text, parts_list)` or None if there's no text to splice into."""
    if content is None:
        return None
    parts = list(getattr(content, "parts", []) or [])
    if not parts:
        return None
    pieces = [getattr(p, "text", "") or "" for p in parts]
    if not any(pieces):
        return None
    return "".join(pieces), parts


def _splice_markers(text: str, splices: list[tuple[int, str]]) -> str:
    """Insert each `marker` at byte offset `end_index` (UTF-16 code units —
    matches the segment indices the model returns). We splice in reverse
    order so earlier offsets aren't shifted by later inserts."""
    if not splices or not text:
        return text
    # Vertex grounding segment indices are byte offsets into the UTF-8
    # encoded response. Convert text -> bytes, splice, convert back.
    encoded = text.encode("utf-8")
    out_parts: list[bytes] = []
    last = len(encoded)
    for end_index, marker in sorted(splices, key=lambda t: t[0], reverse=True):
        if end_index < 0 or end_index > last:
            continue
        out_parts.append(encoded[end_index:last])
        out_parts.append(marker.encode("utf-8"))
        last = end_index
    out_parts.append(encoded[:last])
    out_parts.reverse()
    return b"".join(out_parts).decode("utf-8", errors="replace")


def capture_grounding_callback(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> LlmResponse | None:
    """ADK `after_model_callback`. Translates `grounding_metadata` into
    citation records on session state and rewrites the model's text with
    inline `[N]` markers so the existing `rag_specialist`/orchestrator/
    aggregator pipeline keeps working unchanged.

    Returns a modified `LlmResponse` when markers were spliced; returns
    `None` (= leave response untouched) when there's no grounding data —
    e.g. small-talk turns where the model didn't call retrieval.
    """
    gm = getattr(llm_response, "grounding_metadata", None)
    if gm is None:
        return None
    chunks = list(getattr(gm, "grounding_chunks", []) or [])
    supports = list(getattr(gm, "grounding_supports", []) or [])
    if not chunks:
        return None

    extracted = _extract_grounded_text(getattr(llm_response, "content", None))
    if extracted is None:
        return None
    full_text, parts = extracted
    # Multi-part responses with multiple text parts are rare for grounded
    # answers (Gemini emits a single text part). Bail out of splicing if
    # we'd risk indexing into the wrong part — citations still get written.
    text_part_count = sum(1 for p in parts if getattr(p, "text", None))

    state = callback_context.state
    citations: list[dict] = list(state.get("citations") or [])

    # 1. Build one citation record per grounding_chunk.
    chunk_idx_to_local: dict[int, int] = {}
    for grounding_idx, ch in enumerate(chunks):
        rc = getattr(ch, "retrieved_context", None)
        if rc is None:
            continue
        uri = getattr(rc, "uri", None)
        title = getattr(rc, "title", None) or uri
        chunk_text = getattr(rc, "text", "") or ""
        rag_chunk = getattr(rc, "rag_chunk", None)
        page_start: int | None = None
        page_end: int | None = None
        if rag_chunk is not None:
            page_span = getattr(rag_chunk, "page_span", None)
            if page_span is not None:
                fp = getattr(page_span, "first_page", None)
                lp = getattr(page_span, "last_page", None)
                page_start = int(fp) if fp is not None else None
                page_end = int(lp) if lp is not None else None

        project_id, file_id = _ids_from_uri(uri)
        local_idx = len(citations) + 1
        record = {
            "idx": local_idx,
            "kind": "file",
            "uri": uri,
            "project_id": project_id,
            "file_id": file_id,
            # chunk_id is the dedup key in citation_aggregator. Synthesise
            # from file_id+page+local_idx so identical chunks pulled by two
            # rag_specialist calls collapse to one chip.
            "chunk_id": f"{file_id or uri}:{page_start}:{local_idx}",
            "filename": title,
            "page_start": page_start,
            "page_end": page_end,
            "figure_label": None,
            "image_path": None,
            "score": None,                # legacy field, unused with managed retrieval
            "confidence": None,            # filled below from grounding_supports
            "snippet": chunk_text.strip()[:200],
            # Full chunk text — used by the debug activity panel. snippet
            # stays short because chip previews / file modal lists rely on
            # the 200-char preview shape.
            "text": chunk_text,
        }
        citations.append(record)
        chunk_idx_to_local[grounding_idx] = local_idx

    # 2. Walk grounding_supports: assign per-chunk confidence (max of all
    # supports that cite it) and build [N] splice points.
    splices: list[tuple[int, str]] = []
    for sup in supports:
        seg = getattr(sup, "segment", None)
        end_index = getattr(seg, "end_index", None) if seg is not None else None
        chunk_indices = list(getattr(sup, "grounding_chunk_indices", []) or [])
        confs = list(getattr(sup, "confidence_scores", []) or [])
        markers: list[str] = []
        for j, ci in enumerate(chunk_indices):
            local_idx = chunk_idx_to_local.get(ci)
            if local_idx is None:
                continue
            markers.append(f"[{local_idx}]")
            if j < len(confs):
                rec = citations[local_idx - 1]
                new = float(confs[j])
                cur = rec.get("confidence")
                if cur is None or new > cur:
                    rec["confidence"] = new
        if markers and end_index is not None:
            splices.append((int(end_index), "".join(markers)))

    state["citations"] = citations

    # 3. Splice [N] markers into the response text. Skip splicing for
    # multi-text-part responses (rare with grounding) — citations still
    # got written, the rag_specialist instruction will produce its own
    # markers above us, and we'd rather miss markers than mis-splice them.
    if not splices or text_part_count != 1:
        return None

    target_idx, target_part = next(
        (i, p) for i, p in enumerate(parts) if getattr(p, "text", None)
    )
    new_text = _splice_markers(target_part.text, splices)
    if new_text == target_part.text:
        return None

    new_part = target_part.model_copy(update={"text": new_text})
    new_parts = list(parts)
    new_parts[target_idx] = new_part
    new_content = llm_response.content.model_copy(update={"parts": new_parts})
    return llm_response.model_copy(update={"content": new_content})
