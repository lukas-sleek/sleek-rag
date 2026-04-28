"""Per-project file inventory injected into the chat system prompt.

The model resolves "Dokument A", "in Teil B", filename hints, etc. natively
against this list and passes the matching 8-char file_id prefix to the
`search_chunks` tool — no regex router needed.

The block uses 8-char prefixes (not full UUIDs) to keep prompt tokens down.
The tool handler resolves prefixes back to full UUIDs.
"""
from __future__ import annotations

from app.db import supabase

PREFIX_LEN = 8


def build_inventory_block(project_id: str, user_id: str) -> str:
    """Return a markdown block listing the project's ready files, or empty."""
    res = (
        supabase()
        .table("project_files")
        .select("id,filename,page_count")
        .eq("project_id", project_id)
        .eq("user_id", user_id)
        .eq("status", "ready")
        .order("filename")
        .execute()
    )
    rows = res.data or []
    if not rows:
        return ""

    lines = ["Verfügbare Dokumente in diesem Projekt:"]
    for r in rows:
        prefix = (r["id"] or "").replace("-", "")[:PREFIX_LEN]
        page_count = r.get("page_count")
        pages = f"({page_count} Seiten)" if page_count else "(? Seiten)"
        lines.append(f"- file_id={prefix}  {r['filename']}  {pages}")
    lines.append("")
    lines.append(
        "Verwende file_ids in `search_chunks`, wenn der Nutzer ein Dokument "
        "namentlich nennt (z.B. 'Dokument A', 'in Teil B', oder den "
        "Dateinamen direkt)."
    )
    return "\n".join(lines)


def resolve_file_id_prefixes(
    prefixes: list[str], project_id: str, user_id: str
) -> list[str]:
    """Map 8-char id prefixes back to full UUIDs scoped to the project.

    Unknown prefixes are silently dropped; the model gets back an empty
    `results: []` for that filter combination and can re-plan.
    """
    cleaned = [p.strip().replace("-", "").lower() for p in prefixes if p]
    cleaned = [p for p in cleaned if p]
    if not cleaned:
        return []
    res = (
        supabase()
        .table("project_files")
        .select("id")
        .eq("project_id", project_id)
        .eq("user_id", user_id)
        .execute()
    )
    full_ids = [r["id"] for r in (res.data or [])]
    out: list[str] = []
    for fid in full_ids:
        bare = fid.replace("-", "").lower()
        for p in cleaned:
            if bare.startswith(p):
                out.append(fid)
                break
    return out
