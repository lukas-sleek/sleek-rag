"""Retrieval helpers over document_chunks.

Building blocks for the agentic `search_chunks` tool (`app/tools/search.py`).
The legacy regex router was deleted in plan 14 — the model decides what to
retrieve via structured filters and the SQL helpers below back the unified
`match_chunks_filtered` RPC.

Projektanalyse v1 still uses `_by_vector` directly per question. Plan 15
will migrate it to `search_chunks` so it can also benefit from filename-
aware filters.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.db import supabase
from app.gemini_client import gemini_client


@dataclass
class RetrievedChunk:
    chunk_id: str
    file_id: str
    filename: str
    project_id: str
    content: str
    page_start: int
    page_end: int
    figure_label: str | None
    block_type: str
    score: float
    image_path: str | None = None

    def to_citation(self) -> dict:
        snippet = self.content[:200] + ("…" if len(self.content) > 200 else "")
        return {
            "chunk_id": self.chunk_id,
            "file_id": self.file_id,
            "filename": self.filename,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "snippet": snippet,
            "figure_label": self.figure_label,
            "image_path": self.image_path,
            "score": self.score,
        }


_CHUNK_COLS = (
    "id,file_id,project_id,content,page_start,page_end,"
    "figure_label,block_type,project_files(filename)"
)


def _by_figure_label(label: str, project_id: str) -> list[RetrievedChunk]:
    res = (
        supabase()
        .table("document_chunks")
        .select(_CHUNK_COLS)
        .eq("project_id", project_id)
        .eq("figure_label", label)
        .execute()
    )
    return [_row_to_chunk(r, score=1.0) for r in (res.data or [])]


def _by_page(page: int, project_id: str) -> list[RetrievedChunk]:
    res = (
        supabase()
        .table("document_chunks")
        .select(_CHUNK_COLS)
        .eq("project_id", project_id)
        .lte("page_start", page)
        .gte("page_end", page)
        .execute()
    )
    return [_row_to_chunk(r, score=1.0) for r in (res.data or [])]


def _by_heading(prefix: str, project_id: str) -> list[RetrievedChunk]:
    res = (
        supabase()
        .rpc(
            "chunks_by_heading_prefix",
            {"p_project_id": project_id, "p_prefix": prefix},
        )
        .execute()
    )
    return [_rpc_row_to_chunk(r, score=0.9) for r in (res.data or [])]


def _by_vector(
    query: str, project_id: str, top_k: int, block_type: str | None
) -> list[RetrievedChunk]:
    emb = (
        gemini_client()
        .embeddings.create(
            model=settings.gemini_embedding_model,
            input=query,
            dimensions=settings.gemini_embedding_dim,
        )
        .data[0]
        .embedding
    )
    res = (
        supabase()
        .rpc(
            "match_chunks",
            {
                "p_project_id": project_id,
                "p_embedding": emb,
                "p_top_k": top_k,
                "p_block_type": block_type,
            },
        )
        .execute()
    )
    return [
        _rpc_row_to_chunk(r, score=float(r.get("similarity", 0.0)))
        for r in (res.data or [])
    ]


def _attach_images(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    if not chunks:
        return []
    chunk_ids = [c.chunk_id for c in chunks]
    res = (
        supabase()
        .table("chunk_images")
        .select("chunk_id,storage_path")
        .in_("chunk_id", chunk_ids)
        .execute()
    )
    by_chunk = {i["chunk_id"]: i["storage_path"] for i in (res.data or [])}
    for c in chunks:
        c.image_path = by_chunk.get(c.chunk_id)
    return chunks


def _row_to_chunk(row: dict, *, score: float) -> RetrievedChunk:
    pf = row.get("project_files") or {}
    if isinstance(pf, list):
        pf = pf[0] if pf else {}
    filename = pf.get("filename", "?")
    return RetrievedChunk(
        chunk_id=row["id"],
        file_id=row["file_id"],
        filename=filename,
        project_id=row["project_id"],
        content=row["content"],
        page_start=row["page_start"],
        page_end=row["page_end"],
        figure_label=row.get("figure_label"),
        block_type=row["block_type"],
        score=score,
    )


def _rpc_row_to_chunk(row: dict, *, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=row["id"],
        file_id=row["file_id"],
        filename=row.get("filename") or "?",
        project_id=row["project_id"],
        content=row["content"],
        page_start=row["page_start"],
        page_end=row["page_end"],
        figure_label=row.get("figure_label"),
        block_type=row["block_type"],
        score=score,
    )
