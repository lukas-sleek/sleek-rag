"""`read_section` — targeted exhaustive read inside one file.

Returns chunks of one section / page range / heading prefix in document
order, no retrieval ranking. Used when retrieval rank misses small fact-
bearing chunks (table headlines, figure captions, single-line list items)
and the agent already knows roughly where to look — typically after a
`list_document_outline` call.

Hard-capped at 30 rows server-side (default 20). The cap is the scalability
guarantee: one call ≈ 5k tokens regardless of corpus size. If the agent
needs more, it chains additional `read_section` calls instead of widening
one.

Returns the same `{results, _chunks}` envelope as `search_chunks` so the
agent loop's chunk-collection / ref-offset logic doesn't have to branch on
tool name.
"""
from __future__ import annotations

import logging

from langsmith import traceable

from app.db import supabase
from app.file_inventory import resolve_file_id_prefixes
from app.retrieval import (
    RetrievedChunk,
    _attach_images,
    _rpc_row_to_chunk,
)

log = logging.getLogger(__name__)


READ_SECTION_TOOL = {
    "type": "function",
    "function": {
        "name": "read_section",
        "description": (
            "Liefert die Chunks eines Abschnitts oder Seitenbereichs einer "
            "Datei in Dokumentreihenfolge — ohne Retrieval-Ranking. "
            "Hard-cap 20 Chunks pro Aufruf (~5k tokens).\n\n"
            "USE WHEN: `search_chunks` hat den relevanten Fakt unter den "
            "Top-K nicht gefunden (typisch für kleine fact-bearing chunks: "
            "Bildunterschriften, Tabellenkopf, einzelne Aufzählungs-"
            "Zeilen). Du kennst (a) den exakten Section-Namen aus einem "
            "vorigen `list_document_outline`-Aufruf, ODER (b) die "
            "Seitenzahl(en), die der Nutzer genannt hat.\n\n"
            "USE WHEN (page-targeted): der Nutzer hat eine Seite oder "
            "einen Seitenbereich konkret genannt — dann reicht "
            "`page_from`/`page_to`, kein `section` nötig.\n\n"
            "USE SIBLING TOOL WHEN: du weisst nicht, welche Section/Seite "
            "du lesen sollst → `list_document_outline` zuerst (Section-"
            "Strings raten endet meist in leeren Treffern). Du suchst "
            "global ohne konkrete Stelle → `search_chunks`.\n\n"
            "PARAMETER: `file_id` PFLICHT. Setze entweder `section` "
            "(nach einem outline-Aufruf), ODER `page_from`+`page_to`, "
            "ODER beide. Der `section`-Wert MUSS aus dem Outline kommen, "
            "nicht aus deinem Vorwissen geraten."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "8-stelliges file_id-Präfix.",
                },
                "section": {
                    "type": "string",
                    "description": (
                        "Section-Heading aus einem vorigen "
                        "`list_document_outline`-Aufruf. Verbatim "
                        "Präfix-Match."
                    ),
                },
                "page_from": {
                    "type": "integer",
                    "description": "Erste Seite des Bereichs (inklusive).",
                },
                "page_to": {
                    "type": "integer",
                    "description": "Letzte Seite des Bereichs (inklusive).",
                },
                "include_page_neighbors": {
                    "type": "boolean",
                    "description": (
                        "Optional, default false. Wenn true, liefere ALLE "
                        "Chunks auf den betroffenen Seiten in Dokument-"
                        "Reihenfolge zurück, nicht nur die Section-/Filter-"
                        "gefilterten. Nützlich, wenn das gesuchte Element "
                        "(z.B. Tabellen-Headline-Zeile mit Total) in einem "
                        "benachbarten Chunk auf derselben Seite liegen "
                        "könnte. Erhöht das Token-Budget — sparsam "
                        "einsetzen, vorzugsweise nach einem ersten "
                        "read_section-Aufruf, der Chunks fand aber nicht "
                        "den gesuchten Fakt (z.B. nur Sub-Zeilen einer "
                        "Tabelle ohne Headline)."
                    ),
                },
            },
            "required": ["file_id"],
            "additionalProperties": False,
        },
    },
}


@traceable(run_type="tool", name="read_section")
def read_section_executor(
    *,
    args: dict,
    project_id: str,
    user_id: str,
    ref_offset: int = 0,
    outlined_file_ids: set[str] | None = None,
) -> dict:
    """Resolve file_id, call `chunks_in_range` with optional section / page
    filters. Returns the same envelope as `search_chunks`. Score is fixed at
    1.0 since these chunks are not ranked by relevance — they are explicitly
    requested by document position.

    Plan 17.4 T4: when `section` is set we enforce that
    `list_document_outline` was called on the same file_id earlier in the
    turn. The chat agent loop threads `outlined_file_ids` (a set of full
    UUIDs) into the executor; when the set is provided and the resolved
    file_id is missing from it, we return a `section_without_outline`
    structured-error envelope. Page-targeted reads (no `section`) bypass
    the gate. When `outlined_file_ids is None` the gate is disabled —
    legacy callers (Projektanalyse, tests not exercising the loop) keep
    working unchanged.
    """
    _ = project_id  # ownership is enforced inside the RPC via user_id

    raw_file_id = (args.get("file_id") or "").strip()
    if not raw_file_id:
        return {
            "results": [],
            "error": {
                "code": "missing_required_argument",
                "argument": "file_id",
                "guidance": (
                    "Wähle ein 8-stelliges file_id-Präfix aus der "
                    "Inventarliste im System-Prompt und rufe das Tool "
                    "erneut auf."
                ),
            },
        }

    resolved = resolve_file_id_prefixes([raw_file_id], project_id, user_id)
    if not resolved:
        return {
            "results": [],
            "error": {
                "code": "unknown_file_id",
                "argument": "file_id",
                "guidance": (
                    "Das angegebene file_id-Präfix existiert nicht in "
                    "der Inventarliste. Wähle ein gültiges Präfix aus "
                    "der Dokumentenliste im System-Prompt."
                ),
            },
        }
    full_file_id = resolved[0]

    section = (args.get("section") or "").strip() or None

    if (
        section
        and outlined_file_ids is not None
        and full_file_id not in outlined_file_ids
    ):
        return {
            "results": [],
            "_chunks": [],
            "error": {
                "code": "section_without_outline",
                "argument": "section",
                "guidance": (
                    f"Du hast `read_section(section='{section}')` "
                    f"aufgerufen, ohne vorher `list_document_outline` "
                    f"auf der Datei '{raw_file_id}' auszuführen. "
                    "Section-Namen müssen aus dem Outline stammen — "
                    "Raten führt zu leeren Treffern. Rufe ZUERST "
                    f"`list_document_outline(file_id='{raw_file_id}')` "
                    "auf, lies die zurückgegebenen Section-Namen, und "
                    "rufe `read_section` dann mit einem dieser Namen "
                    "auf. Alternativ: `read_section` mit `page_from`/"
                    "`page_to` ohne `section` — dann ist kein Outline "
                    "nötig."
                ),
            },
        }
    page_from = args.get("page_from")
    page_to = args.get("page_to")
    try:
        page_from_int = int(page_from) if page_from is not None else None
    except (TypeError, ValueError):
        page_from_int = None
    try:
        page_to_int = int(page_to) if page_to is not None else None
    except (TypeError, ValueError):
        page_to_int = None

    try:
        res = (
            supabase()
            .rpc(
                "chunks_in_range",
                {
                    "p_file_id": full_file_id,
                    "p_user_id": user_id,
                    "p_page_from": page_from_int,
                    "p_page_to": page_to_int,
                    "p_heading_prefix": section,
                    "p_limit": 20,
                },
            )
            .execute()
        )
    except Exception as exc:
        log.warning("read_section: rpc failed: %s", exc)
        return {
            "results": [],
            "error": {
                "code": "rpc_failed",
                "guidance": (
                    "Der read_section-Service ist gerade nicht erreichbar. "
                    "Versuche es in ein paar Sekunden erneut, oder "
                    "fall back auf `search_chunks` mit `file_ids=["
                    f"'{raw_file_id}']`."
                ),
            },
        }

    rows = res.data or []
    chunks: list[RetrievedChunk] = [_rpc_row_to_chunk(r, score=1.0) for r in rows]

    # Plan 17.4.1 F8b: when the agent passes include_page_neighbors=true,
    # expand the result to ALL chunks on the same pages (in document
    # order). Lets the model see the full page context — typically the
    # Tabellen-Headline-Zeile with the Total when read_section initially
    # returned only sub-rows. Hard cap stays at 30 to bound the token cost.
    if args.get("include_page_neighbors") and chunks:
        pages = sorted({c.page_start for c in chunks})
        seen_ids: set[str] = set()
        expanded: list[RetrievedChunk] = []
        for p in pages:
            try:
                page_res = (
                    supabase()
                    .rpc(
                        "chunks_on_page",
                        {
                            "p_file_id": full_file_id,
                            "p_user_id": user_id,
                            "p_page": int(p),
                        },
                    )
                    .execute()
                )
            except Exception as exc:
                log.warning(
                    "read_section: chunks_on_page rpc failed for page %s: %s",
                    p,
                    exc,
                )
                continue
            for r in page_res.data or []:
                cid = r.get("id")
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    expanded.append(_rpc_row_to_chunk(r, score=1.0))
        if expanded:
            chunks = expanded[:30]

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
                "excerpt": excerpt,
            }
        )
    return {"results": results, "_chunks": chunks}
