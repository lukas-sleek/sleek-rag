"""Custom FunctionTool that retrieves chunks from a per-project Vertex RAG corpus.

Plan 19.0 T2.

Pivot away from the originally planned VertexAiRagRetrieval subclass:
T0 probe 2 verified that the managed tool's process_llm_request always
registers a server-side Tool(retrieval=...) for Gemini 2.x — run_async
is never called, so an override is dead code. Instead we declare a plain
FunctionTool whose async callable:
  1. Calls rag.retrieval_query (sync wrapper, dispatched to a thread).
     We use the high-level `retrieval_query` rather than the lower-level
     `async_retrieve_contexts` because the canonical Vertex docs example
     uses retrieval_query, and async_retrieve_contexts has an
     under-documented proto shape that returned `InvalidArgument` in
     live testing.
  2. Builds structured per-chunk records with regex-extracted page +
     figure metadata.
  3. Writes the records into tool_context.state["citations"] for the
     post-run aggregator in chats.py to dedupe + renumber.
  4. Returns a structured dict to the LLM so it can place [N] markers
     and reason about which file/page the chunk came from.

The corpus is closed over by the factory — one FunctionTool per cached
AdkApp.
"""
import asyncio
import logging
import re
from typing import Any

from google.adk.tools import FunctionTool, ToolContext

# No `from __future__ import annotations` here: ADK's tool-declaration
# builder evaluates parameter annotations via `typing.get_type_hints`,
# which fails on stringised forwards refs that reference ADK's own
# ToolContext. Keeping the annotation as a real type makes registration
# work end-to-end.
from langsmith import traceable
from vertexai.preview import rag

log = logging.getLogger(__name__)


# Boundary regex — operates on parser output ([Seite N] / [Abb. N: ...]
# markers injected by the LLM Parser during ingestion). Never applied to
# the LLM's answer prose.
_PAGE_RE = re.compile(r"\[Seite\s+(\d+)\]")
_FIGURE_RE = re.compile(r"\[Abb\.?\s*(\d+(?:\.\d+)*)\s*:\s*([^\]]+)\]")

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


@traceable(run_type="retriever", name="search_project_documents")
def _retrieve_sync(
    *, query: str, corpus_name: str, top_k: int
) -> Any:
    """Inner helper: pure sync Vertex RAG retrieval, traced via langsmith.

    Kept separate from the FunctionTool callable because @traceable widens
    the wrapped function's signature with `config` / `langsmith_extra`
    kwargs. ADK's _get_declaration() inspects the FunctionTool's func and
    rejects the resulting schema. By doing the langsmith wrap on this
    inner helper, ADK only sees the clean outer signature.
    """
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
    """Build a per-corpus search_project_documents FunctionTool.

    The returned tool exposes a single argument query: str to the LLM and
    yields chunks plus side-channel citations (in tool_context.state).
    """

    async def search_project_documents(
        query: str, tool_context: ToolContext
    ) -> dict[str, Any]:
        """Search the project's RAG corpus and return ranked chunks.

        Args:
            query: A self-contained natural-language search query in the
                project's working language. Pronouns and references must
                already be resolved by the caller.

        Returns:
            On hit: {"status": "ok", "chunks": [{"idx", "filename",
            "page_start", "page_end", "text"}, ...]}.
            On miss: {"status": "no_results", "chunks": []}.
            The LLM places [idx] markers inline in its answer; the
            aggregator in chats.py renumbers them globally after the run.
        """
        log.info(
            "search_project_documents: corpus=%s top_k=%s query=%r",
            corpus_name, top_k, query[:120],
        )
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

        # Per-turn citation index. The aggregator in chats.py renumbers
        # globally after the run (handles dedup across multiple
        # rag_specialist calls in one orchestrator turn).
        citations: list[dict] = tool_context.state.setdefault("citations", [])
        next_idx = len(citations) + 1

        out_chunks: list[dict] = []
        for ctx in contexts:
            text = ctx.text or ""
            pages = [int(m.group(1)) for m in _PAGE_RE.finditer(text)]
            fig = _FIGURE_RE.search(text)

            uri = getattr(ctx, "source_uri", None)
            project_id, file_id = _ids_from_uri(uri)
            page_start = pages[0] if pages else None
            record = {
                "idx": next_idx,
                "kind": "file",
                "uri": uri,
                "project_id": project_id,
                "file_id": file_id,
                # chunk_id is the frontend's dedup key; synthesise from
                # file_id+page+score so identical chunks pulled by two
                # rag_specialist calls collapse to one chip.
                "chunk_id": f"{file_id or uri}:{page_start}:{next_idx}",
                "filename": getattr(ctx, "source_display_name", None)
                or getattr(ctx, "source_uri", None),
                "page_start": page_start,
                "page_end": pages[-1] if pages else None,
                "figure_label": f"Abb. {fig.group(1)}" if fig else None,
                "image_path": None,
                "score": getattr(ctx, "score", None),
                "snippet": text.strip()[:200],
            }
            citations.append(record)
            out_chunks.append(
                {
                    "idx": next_idx,
                    "filename": record["filename"],
                    "page_start": record["page_start"],
                    "page_end": record["page_end"],
                    "text": text,
                }
            )
            next_idx += 1

        return {"status": "ok", "chunks": out_chunks}

    return FunctionTool(func=search_project_documents)
