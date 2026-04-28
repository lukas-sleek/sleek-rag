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
            "Globale, gerankte Suche über alle Projektdokumente. "
            "Liefert die top-K relevantesten Chunks mit `ref`-Nummern für "
            "Zitate.\n\n"
            "USE WHEN: jede inhaltliche Frage. Standard-Einstieg ins "
            "Retrieval. Bei Aggregations-/Sammelfragen ('welche Bauherren', "
            "'alle Termine', 'wer leitet') darfst und sollst du MEHRERE "
            "search_chunks-Aufrufe parallel im selben Turn emittieren — "
            "z.B. einen pro Synonym/Facette/file_id. Gemini führt sie "
            "parallel aus.\n\n"
            "USE SIBLING TOOL WHEN: du kennst bereits einen konkreten "
            "Section-Namen und willst den Abschnitt verbatim lesen → "
            "`read_section`. Du brauchst eine Übersicht, welche Sections "
            "in einer Datei existieren → `list_document_outline`. Du "
            "kommst mit Per-Tool-Deep-Dive nicht weiter und brauchst eine "
            "Volltext-Synthese → `run_projektanalyse_v2`.\n\n"
            "PARAMETER `query` ist PFLICHT — leite einen kurzen Such-"
            "String aus der Frage des Nutzers ab. Beispiel-"
            "Transformationen:\n"
            "  - 'Welche Termine sind vorgesehen?' → query='Termine "
            "Meilensteine'\n"
            "  - 'Was ist die Bausumme?' → query='Bausumme Baukosten "
            "Total'\n"
            "  - 'Wer ist der Projektleiter?' → query='Projektleiter "
            "Name'\n"
            "Niemals leer, niemals weglassen, niemals den Nutzer um "
            "die Suchanfrage bitten."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "PFLICHT. Kurzer Such-String, abgeleitet aus der "
                        "Frage des Nutzers. Beispiele: 'Bausumme', "
                        "'Projektleiter', 'Schnittstellenprojekte Bushof'."
                    ),
                },
                "file_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "8-stellige file_id-Präfixe aus der Inventarliste. "
                        "Setzen, wenn der Nutzer ein Dokument namentlich "
                        "nennt ('Dokument A', 'Teil B', Dateiname). Bei "
                        "Aggregations-Fragen kannst du mehrere parallele "
                        "search_chunks-Aufrufe machen — je einen pro "
                        "Datei."
                    ),
                },
                "page": {
                    "type": "integer",
                    "description": (
                        "Exakte Seitenzahl. Treffer auf genau dieser Seite "
                        "werden bevorzugt."
                    ),
                },
                "figure_label": {
                    "type": "string",
                    "description": (
                        "Abbildungs-Label, z.B. 'Abbildung 3.6'."
                    ),
                },
                "section": {
                    "type": "string",
                    "description": (
                        "Heading-Präfix, z.B. '3.6' oder 'Projektorganisation'."
                    ),
                },
                "block_type": {
                    "type": "string",
                    "enum": ["paragraph", "figure", "table"],
                    "description": "Filtert auf Blocktyp.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximale Trefferzahl (1-25, Default 15).",
                },
                "expand_synonyms": {
                    "type": "boolean",
                    "description": (
                        "Optional. true → das Tool generiert intern 2-3 "
                        "Synonym-Suchanfragen (eine extra Gemini-Aufruf) "
                        "und merged die Ergebnisse via RRF, bevor "
                        "rerankt wird. Hilfreich bei deutschen Domain-"
                        "Begriffen mit Synonym-Clustern (Bauherr↔"
                        "Grundeigentümer, Bausumme↔Baukosten, Drittprojekt"
                        "↔Schnittstellenprojekt). Sparsam einsetzen — nur "
                        "wenn ein erster Aufruf ohne expand_synonyms zu "
                        "wenig Treffer brachte. Default false."
                    ),
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}


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
        return {
            "results": [],
            "error": {
                "code": "missing_required_argument",
                "argument": "query",
                "guidance": (
                    "Übersetze die Frage des Nutzers in einen kurzen "
                    "Such-String (z.B. Frage 'Was ist die Bausumme?' → "
                    "query='Bausumme') und rufe `search_chunks` erneut "
                    "auf. Frage NIEMALS den Nutzer nach einer Suchanfrage."
                ),
            },
        }

    raw_top_k = args.get("top_k") or 15
    try:
        top_k = max(1, min(int(raw_top_k), 25))
    except (TypeError, ValueError):
        top_k = 15

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
            return {
                "results": [],
                "error": {
                    "code": "unknown_file_id",
                    "argument": "file_ids",
                    "guidance": (
                        "Die angegebenen file_id-Präfixe existieren nicht "
                        "in der Inventarliste des Projekts. Wähle 8-stellige "
                        "Präfixe aus der Dokumentenliste im System-Prompt, "
                        "oder lass `file_ids` weg, um über alle Dokumente "
                        "zu suchen."
                    ),
                },
            }
        full_file_ids = resolved

    mode = (settings.retrieval_mode or "hybrid").lower()

    # Plan 17.2: query expansion is now opt-in via the `expand_synonyms`
    # parameter — the agent decides when synonym fan-out is worth the extra
    # Gemini call. Vector-only mode disables it (FTS is degenerate, no
    # synonyms help). Structural filters (page/figure/file_ids) implicitly
    # skip it because the search is already pinned to a known cluster, but
    # we leave that decision to the model now too.
    expand_synonyms = bool(args.get("expand_synonyms"))
    sub_queries: list[str] = []
    if mode != "vector_only" and expand_synonyms and settings.query_expansion:
        sub_queries = _expand_query(query)

    queries = [query] + sub_queries
    embeddings = _embed_many(queries)
    if embeddings is None:
        return {
            "results": [],
            "error": {
                "code": "embedding_failed",
                "guidance": (
                    "Der Embedding-Service ist gerade nicht erreichbar. "
                    "Versuche es in ein paar Sekunden erneut oder mit einer "
                    "anderen, kürzeren Suchanfrage."
                ),
            },
        }

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
