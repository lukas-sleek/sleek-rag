"""FunctionTool that retrieves chunks from a per-project Vertex RAG corpus.

Plan 20.0 — serverless mode in us-central1.

Retrieval response on serverless does NOT carry page spans, headings, or
figure metadata; `source_uri` is a Vertex temp-bucket URI (useless for
mapping back to our GCS layout) and `source_display_name` is empty.
The friendly filename comes from `chunk.file_id` -> RagFile.display_name
via list_rag_files (cached).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from google.adk.tools import FunctionTool, ToolContext

from langsmith import traceable
from vertexai.preview import rag

from app.rag_corpus import _init_vertex_for

log = logging.getLogger(__name__)

_DEFAULT_TOP_K = 10
_LIST_FILES_TTL_SEC = 60.0

# corpus_name -> (expires_at_epoch, {file_id: display_name})
_filename_cache: dict[str, tuple[float, dict[str, str]]] = {}


def _file_id_from_chunk(chunk_obj) -> str | None:
    """Extract the numeric file_id from a retrieval chunk."""
    if chunk_obj is None:
        return None
    fid = getattr(chunk_obj, "file_id", None)
    return str(fid) if fid else None


def _filename_map(corpus_name: str, *, force_refresh: bool = False) -> dict[str, str]:
    """Return {file_id: display_name} for the corpus. 60s in-process cache."""
    now = time.time()
    cached = _filename_cache.get(corpus_name)
    if cached and not force_refresh and cached[0] > now:
        return cached[1]
    out: dict[str, str] = {}
    try:
        for f in rag.list_files(corpus_name):
            file_id = f.name.rsplit("/", 1)[-1]
            out[file_id] = f.display_name or ""
    except Exception:
        log.exception("list_files failed for corpus %s", corpus_name)
        # fall through with empty map; chunks will render with file_id as label
    _filename_cache[corpus_name] = (now + _LIST_FILES_TTL_SEC, out)
    return out


@traceable(run_type="retriever", name="search_project_documents")
def _retrieve_sync(*, query: str, corpus_name: str, top_k: int) -> Any:
    return rag.retrieval_query(
        text=query,
        rag_resources=[rag.RagResource(rag_corpus=corpus_name)],
        rag_retrieval_config=rag.RagRetrievalConfig(top_k=top_k),
    )


def make_search_project_documents_tool(
    corpus_name: str,
    *,
    top_k: int = _DEFAULT_TOP_K,
) -> FunctionTool:
    """Build a per-corpus search_project_documents FunctionTool."""

    async def search_project_documents(
        query: str, tool_context: ToolContext
    ) -> dict[str, Any]:
        """Search the project's RAG corpus and return ranked chunks.

        Args:
            query: A self-contained natural-language search query in the
                project's working language. Pronouns and references must
                already be resolved by the caller.

        Returns:
            On hit: {"status": "ok", "chunks": [{"idx", "filename", "text"}, ...]}.
            On miss: {"status": "no_results", "chunks": []}.
            The LLM places [idx] markers inline; the aggregator in chats.py
            renumbers them globally after the run.
        """
        log.info(
            "search_project_documents: corpus=%s top_k=%s query=%r",
            corpus_name, top_k, query[:120],
        )
        # Re-init Vertex at the corpus's region (legacy EU corpora work).
        await asyncio.to_thread(_init_vertex_for, corpus_name)
        try:
            response = await asyncio.to_thread(
                _retrieve_sync,
                query=query,
                corpus_name=corpus_name,
                top_k=top_k,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "rag retrieval failed for corpus=%s query=%r: %s: %s",
                corpus_name, query[:120], type(exc).__name__, exc,
            )
            raise

        contexts = response.contexts.contexts
        if not contexts:
            return {"status": "no_results", "chunks": []}

        # Build file_id -> display_name map. If a chunk's file_id misses,
        # bypass the cache once to pick up newly-imported files.
        names = await asyncio.to_thread(_filename_map, corpus_name)
        unknown = {
            _file_id_from_chunk(ctx.chunk)
            for ctx in contexts
            if _file_id_from_chunk(ctx.chunk) and _file_id_from_chunk(ctx.chunk) not in names
        }
        if unknown:
            names = await asyncio.to_thread(_filename_map, corpus_name, force_refresh=True)

        citations: list[dict] = tool_context.state.setdefault("citations", [])
        next_idx = len(citations) + 1

        out_chunks: list[dict] = []
        for ctx in contexts:
            text = ctx.text or ""
            file_id = _file_id_from_chunk(ctx.chunk)
            chunk_id = str(getattr(ctx.chunk, "chunk_id", "")) if ctx.chunk else ""
            filename = names.get(file_id or "", "") or file_id or "Dokument"
            record = {
                "idx": next_idx,
                "kind": "file",
                "filename": filename,
                "snippet": text,
                "score": getattr(ctx, "score", None),
                "chunk_id": chunk_id or f"{file_id}:{next_idx}",
                "file_id": file_id,
            }
            citations.append(record)
            score = getattr(ctx, "score", None)
            out_chunks.append({
                "idx": next_idx,
                "filename": filename,
                "text": text,
                # `score` is exposed to the LLM and to the activity-panel
                # trace frame so debug users can see retrieval confidence.
                # Vertex returns a relevance score in [0, 1]; None when the
                # backend didn't supply one.
                "score": float(score) if score is not None else None,
            })
            next_idx += 1

        return {"status": "ok", "chunks": out_chunks}

    return FunctionTool(func=search_project_documents)
