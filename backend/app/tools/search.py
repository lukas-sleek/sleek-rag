"""`search_chunks` — the only retrieval tool the chat model has.

The model decides what to retrieve via structured filters; the backend never
parses the user's text. Filter dispatch:

| filter set on call         | path                                     |
|----------------------------|------------------------------------------|
| `figure_label`             | exact-match WHERE, hybrid-rank within    |
| `page`                     | overlap WHERE, page-bucket-rank then RRF |
| `section`                  | heading_path prefix, hybrid-rank         |
| only `query`               | hybrid (vector + FTS) via match_chunks_hybrid |

`file_ids` (8-char prefixes from the inventory) and `block_type` apply to all
branches. A single SQL function (`match_chunks_hybrid`, migration 0014)
handles every combination and returns RRF-fused vector + FTS results.

Plan 16 T4: pulls `pre_rerank_k` candidates from the RPC, then reranks down
to the model's requested `top_k` via Vertex AI Ranking API.
`RETRIEVAL_MODE=vector_only` reproduces plan-14 behavior (degenerate FTS, no
rerank) for debugging / regression-fallback.

Plan 16 T6: short "welche/wer" questions are expanded to 2-3 synonym
sub-queries via a fast Gemini call. Each sub-query (plus the original) hits
the hybrid RPC; their candidate lists are RRF-merged by chunk_id before the
final rerank pass. Closes the synonym-cluster gap (Bauherr ↔
Grundeigentümer, Drittprojekt ↔ Schnittstellenprojekt) deterministically —
doesn't depend on the chat agent deciding to retry. Skipped when any
structural filter (file_ids/page/figure_label/section) is set, since those
already narrow the search to a known cluster.
"""
from __future__ import annotations

import logging
import re

from langsmith import traceable

from app import ranking_client
from app.config import settings
from app.db import supabase
from app.file_inventory import resolve_file_id_prefixes
from app.gemini_client import gemini_client
from app.retrieval import (
    RetrievedChunk,
    _attach_images,
    _rpc_row_to_chunk,
)

log = logging.getLogger(__name__)


SEARCH_CHUNKS_TOOL = {
    "type": "function",
    "function": {
        "name": "search_chunks",
        "description": (
            "Sucht in den Projektdokumenten nach relevanten Stellen. "
            "Rufe dieses Tool auf, sobald die Frage Inhaltliches aus den "
            "Dokumenten betrifft. Nutze `file_ids`, wenn der Nutzer ein "
            "konkretes Dokument nennt (z.B. 'Dokument A', 'in Teil B'); "
            "nutze `page` für Seitenangaben; `figure_label` für 'Abbildung 3.6'; "
            "`section` für Kapitel/Abschnitt 3.6. Lass Filter weg, wenn unklar."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natürlichsprachige Suchanfrage. Pflicht.",
                },
                "file_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "8-stellige file_id-Präfixe aus der Dokumenten-Liste, "
                        "um die Suche auf bestimmte Dateien einzuschränken."
                    ),
                },
                "page": {
                    "type": "integer",
                    "description": (
                        "Exakte Seitenzahl. Treffer mit page_start == "
                        "page_end == page werden bevorzugt."
                    ),
                },
                "figure_label": {
                    "type": "string",
                    "description": (
                        "Abbildungs-Label, z.B. 'Figure 3.6' oder 'Abbildung 3.6'."
                    ),
                },
                "section": {
                    "type": "string",
                    "description": "Heading-Präfix, z.B. '3.6' oder 'Installation'.",
                },
                "block_type": {
                    "type": "string",
                    "enum": ["paragraph", "figure", "table"],
                    "description": "Filtert auf Blocktyp.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximale Trefferzahl (1-20, Default 8).",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}


_EXPAND_TRIGGER = re.compile(
    r"\b(welche[mnrs]?|welches|wer|wem|wen)\b",
    re.IGNORECASE,
)

# Domain nouns where synonym recall reliably beats single-query cosine
# (Bauherr ↔ Grundeigentümer, Bausumme ↔ Baukosten/Investitionskosten,
# Termine ↔ Zeitplan/Meilensteine, Honorar ↔ Aufwand/Stunden, etc.). Trigger
# expansion regardless of question shape when one of these surfaces — the
# v2-vs-chat gap on the test corpus tracks this list almost 1:1.
_EXPAND_NOUN_TRIGGER = re.compile(
    r"\b("
    r"bauherr(?:en|n)?|grundeigent(?:ü|ue)mer|auftraggeber|"
    r"bausumme|baukosten|investitionskosten|gesamtkosten|honorar|"
    r"termine?|zeitplan|meilenstein(?:e)?|"
    r"drittprojekt(?:e)?|schnittstellen(?:projekt(?:e)?)?"
    r")\b",
    re.IGNORECASE,
)


def _should_expand(query: str, *, has_structural_filter: bool) -> bool:
    """Trigger query expansion on short, broad questions where single-query
    cosine routinely misses synonym clusters. Two triggers, either suffices:
      - 'welche/wer' interrogative pattern (aggregation questions)
      - one of the high-value domain nouns (Bausumme, Termine, etc.)
    Skipped when a structural filter is set — those already narrow the
    search to a known cluster, expansion is wasted recall.
    """
    if not settings.query_expansion or has_structural_filter:
        return False
    if len(query.split()) > 8:
        return False
    return bool(
        _EXPAND_TRIGGER.search(query) or _EXPAND_NOUN_TRIGGER.search(query)
    )


@traceable(run_type="llm", name="expand_query")
def _expand_query(query: str) -> list[str]:
    """Generate 0-3 synonym sub-queries via a fast Gemini call. Returns an
    empty list on failure or when the model produces nothing usable —
    callers should treat expansion as best-effort additive recall, never
    load-bearing."""
    instruction = (
        "Du erhältst eine deutsche Suchanfrage zu einer Schweizer "
        "Bahn-/Ingenieurprojekt-Ausschreibung. Generiere 2-3 alternative "
        "Suchbegriffe oder Synonyme zum Hauptthema, die zu unterschiedlichen "
        "Wortlauten in deutschen Bauausschreibungen führen können (z.B. "
        "Fachbegriffe, formale Synonyme, übergeordnete Kategorien). "
        "Antworte AUSSCHLIESSLICH mit den Begriffen, kommagetrennt, ohne "
        "Erklärung, ohne Anführungszeichen, ohne Aufzählungszeichen.\n\n"
        "Beispiele:\n"
        "Frage: \"Welche Bauherren sind beteiligt?\"\n"
        "Antwort: Grundeigentümer, Auftraggeber, Projektpartner\n\n"
        "Frage: \"Welche Drittprojekte tangieren?\"\n"
        "Antwort: Schnittstellenprojekt, tangierendes Projekt, Bauvorhaben\n\n"
        "Frage: \"Was ist die Bausumme?\"\n"
        "Antwort: Baukosten, Investitionskosten, Gesamtkosten"
    )
    try:
        resp = gemini_client().chat.completions.create(
            model=settings.gemini_chat_model,
            messages=[
                {"role": "system", "content": instruction},
                {"role": "user", "content": query},
            ],
            extra_body={"reasoning_effort": "none"},
        )
    except Exception as exc:
        log.warning("expand_query: gemini call failed: %s", exc)
        return []

    text = (resp.choices[0].message.content or "").strip()
    if not text:
        return []
    # Tolerate a few separator variants the model occasionally uses.
    text = text.replace(";", ",").replace("\n", ",").replace(" / ", ",")
    raw_parts = [p.strip().strip('"').strip("'") for p in text.split(",")]
    expansions: list[str] = []
    seen_lower = {query.lower().strip()}
    for part in raw_parts:
        if not part or len(part) > 80:
            continue
        low = part.lower()
        if low in seen_lower:
            continue
        seen_lower.add(low)
        expansions.append(part)
        if len(expansions) >= 3:
            break
    return expansions


def _embed_many(queries: list[str]) -> list[list[float]] | None:
    """Embed a batch of queries in one round-trip. Returns None on failure
    (caller should bail rather than fall back to bad embeddings)."""
    try:
        resp = gemini_client().embeddings.create(
            model=settings.gemini_embedding_model,
            input=queries,
            dimensions=settings.gemini_embedding_dim,
        )
    except Exception as exc:
        log.warning("search_chunks: embedding failed: %s", exc)
        return None
    return [item.embedding for item in resp.data]


def _run_hybrid_rpc(
    *,
    project_id: str,
    embedding: list[float],
    query: str,
    top_k: int,
    full_file_ids: list[str] | None,
    block_type: str | None,
    page_int: int | None,
    figure_label: str | None,
    section: str | None,
) -> list[dict]:
    """One hybrid RPC call. Returns raw rows (empty on error)."""
    try:
        res = (
            supabase()
            .rpc(
                "match_chunks_hybrid",
                {
                    "p_project_id": project_id,
                    "p_embedding": embedding,
                    "p_query": query,
                    "p_top_k": top_k,
                    "p_file_ids": full_file_ids,
                    "p_block_type": block_type,
                    "p_page": page_int,
                    "p_figure_label": figure_label,
                    "p_heading_prefix": section,
                },
            )
            .execute()
        )
    except Exception as exc:
        log.warning("search_chunks: rpc failed (q=%r): %s", query[:60], exc)
        return []
    return res.data or []


def _rrf_merge(
    rows_by_query: list[list[dict]], *, k: int = 60
) -> list[dict]:
    """Merge per-query RPC result lists by chunk id via Reciprocal Rank
    Fusion. Returns rows sorted by fused score desc, deduped by id."""
    fused_score: dict[str, float] = {}
    first_row: dict[str, dict] = {}
    for rows in rows_by_query:
        for rank, row in enumerate(rows, start=1):
            cid = row.get("id")
            if not cid:
                continue
            fused_score[cid] = fused_score.get(cid, 0.0) + 1.0 / (k + rank)
            if cid not in first_row:
                first_row[cid] = row
    return [
        first_row[cid]
        for cid, _ in sorted(
            fused_score.items(), key=lambda kv: kv[1], reverse=True
        )
    ]


@traceable(run_type="tool", name="search_chunks")
def execute_search_chunks(
    *,
    args: dict,
    project_id: str,
    user_id: str,
    ref_offset: int = 0,
    pre_rerank_k_override: int | None = None,
) -> dict:
    """Execute one `search_chunks` call. Returns a JSON-ready payload.

    `ref_offset` lets the agent loop number references contiguously across
    multiple tool calls in a single turn (so [1] always means the same chunk
    in the final answer).

    `pre_rerank_k_override` lets the batch path (Projektanalyse v1) widen
    the candidate pool feeding the rerank stage without bumping the global
    config knob. None → fall back to `settings.pre_rerank_k`.
    """
    query = (args.get("query") or "").strip()
    if not query:
        return {"results": [], "error": "missing query"}

    raw_top_k = args.get("top_k") or 8
    try:
        top_k = max(1, min(int(raw_top_k), 20))
    except (TypeError, ValueError):
        top_k = 8

    block_type = args.get("block_type") or None
    page = args.get("page")
    try:
        page_int = int(page) if page is not None else None
    except (TypeError, ValueError):
        page_int = None

    figure_label = (args.get("figure_label") or "").strip() or None
    section = (args.get("section") or "").strip() or None

    file_ids_raw = args.get("file_ids") or []
    if not isinstance(file_ids_raw, list):
        file_ids_raw = []
    full_file_ids: list[str] | None = None
    if file_ids_raw:
        resolved = resolve_file_id_prefixes(
            [str(p) for p in file_ids_raw], project_id, user_id
        )
        if not resolved:
            # Caller named files we don't have. Return empty so the model
            # can re-plan rather than silently widening to all files.
            return {"results": []}
        full_file_ids = resolved

    mode = (settings.retrieval_mode or "hybrid").lower()
    has_structural_filter = bool(
        full_file_ids or page_int or figure_label or section
    )

    # T6: query expansion. Vector-only mode skips it (no synonyms help when
    # FTS is disabled), and any structural filter skips it too — those
    # filters already pin the search to a known cluster.
    sub_queries: list[str] = []
    if mode != "vector_only" and _should_expand(
        query, has_structural_filter=has_structural_filter
    ):
        sub_queries = _expand_query(query)

    queries = [query] + sub_queries
    embeddings = _embed_many(queries)
    if embeddings is None:
        return {"results": [], "error": "embedding_failed"}

    if mode == "vector_only":
        # Pure-vector escape hatch: empty FTS query degrades match_chunks_hybrid
        # to cosine-only, no rerank. Pull exactly top_k. Single query only.
        rpc_top_k = top_k
        do_rerank = False
        rows = _run_hybrid_rpc(
            project_id=project_id,
            embedding=embeddings[0],
            query="",
            top_k=rpc_top_k,
            full_file_ids=full_file_ids,
            block_type=block_type,
            page_int=page_int,
            figure_label=figure_label,
            section=section,
        )
    else:
        base_pre_k = (
            pre_rerank_k_override
            if (pre_rerank_k_override is not None and pre_rerank_k_override > 0)
            else settings.pre_rerank_k
        )
        rpc_top_k = max(top_k, int(base_pre_k))
        do_rerank = True

        per_query_rows: list[list[dict]] = []
        for q, emb in zip(queries, embeddings):
            per_query_rows.append(
                _run_hybrid_rpc(
                    project_id=project_id,
                    embedding=emb,
                    query=q,
                    top_k=rpc_top_k,
                    full_file_ids=full_file_ids,
                    block_type=block_type,
                    page_int=page_int,
                    figure_label=figure_label,
                    section=section,
                )
            )
        if len(queries) == 1:
            rows = per_query_rows[0]
        else:
            # RRF-merge unions across original + sub-queries; cap so the
            # downstream rerank still stays cheap.
            rows = _rrf_merge(per_query_rows)[: rpc_top_k]

    chunks: list[RetrievedChunk] = [
        _rpc_row_to_chunk(r, score=float(r.get("vec_similarity", 0.0) or 0.0))
        for r in rows
    ]

    if do_rerank and chunks:
        # Always rerank with the ORIGINAL query — sub-queries are only for
        # candidate-pool widening, not for final relevance scoring.
        scored = ranking_client.rank(
            query=query,
            documents=[c.content for c in chunks],
            top_n=top_k,
        )
        if scored and any(s != 0.0 for _, s in scored):
            new_chunks: list[RetrievedChunk] = []
            for idx, score in scored:
                if 0 <= idx < len(chunks):
                    c = chunks[idx]
                    c.score = score
                    new_chunks.append(c)
            chunks = new_chunks[:top_k]
        else:
            # Fail-open: rerank unavailable, keep RRF order, trim to top_k.
            chunks = chunks[:top_k]
    else:
        chunks = chunks[:top_k]

    chunks = _attach_images(chunks)

    results = []
    for i, c in enumerate(chunks, 1):
        excerpt = c.content[:280] + ("…" if len(c.content) > 280 else "")
        prefix = c.file_id.replace("-", "")[:8]
        results.append(
            {
                "ref": ref_offset + i,
                "chunk_id": c.chunk_id,
                "file_id": prefix,
                "filename": c.filename,
                "page_start": c.page_start,
                "page_end": c.page_end,
                "figure_label": c.figure_label,
                "block_type": c.block_type,
                "similarity": round(c.score, 4),
                "excerpt": excerpt,
            }
        )
    return {"results": results, "_chunks": chunks}
