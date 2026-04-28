"""`list_document_outline` — heading-tree navigator for one file.

Returns a compact list of distinct heading entries (one row per heading) with
page span and chunk count. The agent calls this *before* `read_section` to
see what sections exist in a file it hasn't yet hit via `search_chunks`.

The payload is tiny (a few hundred tokens for a typical doc) and contains no
chunk content — this tool is for navigation, not citation. Chunks come back
through `read_section` or `search_chunks`, which is where the `ref` accumulator
picks them up.
"""
from __future__ import annotations

import logging

from langsmith import traceable

from app.db import supabase
from app.file_inventory import resolve_file_id_prefixes

log = logging.getLogger(__name__)


LIST_DOCUMENT_OUTLINE_TOOL = {
    "type": "function",
    "function": {
        "name": "list_document_outline",
        "description": (
            "Liefert die Gliederung einer Datei: alle distinkten "
            "Headings mit Seitenbereich und Chunk-Anzahl. Tiny payload "
            "(~few hundred tokens), Navigation-only — keine Chunk-Inhalte.\n\n"
            "USE WHEN: du planst einen `read_section(section=...)`-Aufruf "
            "und brauchst den exakten Section-Namen aus der Datei (Section-"
            "Strings raten endet meist in leeren Treffern). Auch wenn "
            "`search_chunks` eine Datei aus der Inventarliste, von der du "
            "Treffer erwartet hättest, nicht abgedeckt hat.\n\n"
            "USE SIBLING TOOL WHEN: du willst Chunk-Inhalt lesen → "
            "`read_section` (verbatim section read) oder `search_chunks` "
            "(globale Suche). Dieses Tool gibt nur die Heading-Struktur "
            "zurück, keine Inhalte."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "8-stelliges file_id-Präfix aus der Inventarliste.",
                },
            },
            "required": ["file_id"],
            "additionalProperties": False,
        },
    },
}


@traceable(run_type="tool", name="list_document_outline")
def list_document_outline_executor(
    *,
    args: dict,
    project_id: str,
    user_id: str,
    ref_offset: int = 0,
) -> dict:
    """Resolve the file_id prefix, call `document_outline`, return one row per
    distinct heading. `ref_offset` is accepted for symmetry with the other
    retrieval tools but unused — this tool emits no `ref`-bearing chunks.
    """
    _ = ref_offset  # navigation-only tool; no ref allocation

    raw_file_id = (args.get("file_id") or "").strip()
    if not raw_file_id:
        return {
            "outline": [],
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
            "outline": [],
            "error": {
                "code": "unknown_file_id",
                "argument": "file_id",
                "guidance": (
                    "Das angegebene file_id-Präfix existiert nicht in "
                    "der Inventarliste des Projekts. Wähle ein gültiges "
                    "8-stelliges Präfix aus der Dokumentenliste im "
                    "System-Prompt."
                ),
            },
        }
    full_file_id = resolved[0]

    try:
        res = (
            supabase()
            .rpc(
                "document_outline",
                {"p_file_id": full_file_id, "p_user_id": user_id},
            )
            .execute()
        )
    except Exception as exc:
        log.warning("list_document_outline: rpc failed: %s", exc)
        return {
            "outline": [],
            "error": {
                "code": "rpc_failed",
                "guidance": (
                    "Der Outline-Service ist gerade nicht erreichbar. "
                    "Versuch es in ein paar Sekunden erneut, oder "
                    "fall back auf `search_chunks` mit `file_ids=["
                    f"'{raw_file_id}']`."
                ),
            },
        }

    rows = res.data or []
    outline = []
    for r in rows:
        hp = r.get("heading_path") or []
        # `document_outline` returns a single-element array per row; flatten
        # to a string for the model so the outline reads as a tidy list.
        heading = hp[0] if isinstance(hp, list) and hp else ""
        outline.append(
            {
                "heading": heading,
                "page_start": r.get("page_start"),
                "page_end": r.get("page_end"),
                "chunk_count": r.get("chunk_count"),
            }
        )

    return {"file_id": raw_file_id, "outline": outline}
