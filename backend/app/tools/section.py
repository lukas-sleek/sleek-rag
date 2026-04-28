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
) -> dict:
    """Resolve file_id, call `chunks_in_range` with optional section / page
    filters. Returns the same envelope as `search_chunks`. Score is fixed at
    1.0 since these chunks are not ranked by relevance — they are explicitly
    requested by document position.
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
