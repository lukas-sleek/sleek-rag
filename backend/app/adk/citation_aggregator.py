"""Per-turn citation deduplication + global renumbering (plan 19.0 T9c).

Each rag_specialist call returns chunks with per-call `idx` values starting
from 1 (or from len(state["citations"]) if multiple specialists run in one
turn — see retrieval_tool.py). The orchestrator's [N] markers reference
those `idx` values verbatim.

After the run, dedupe_and_renumber walks the accumulated citations,
collapses duplicates (same uri + page + snippet[:80]) to one canonical
entry, and produces a remap from old idx -> new idx. rewrite_refs then
walks the answer text and substitutes `[old]` -> `[new]`.
"""
from __future__ import annotations

import re

_REF_RE = re.compile(r"\[(\d+)\]")


def _dedupe_key(c: dict) -> tuple:
    """Per-record dedupe key. Web vs file collapse independently:
    - web: (kind, url)  — same URL cited twice = one chip
    - file: (kind, uri, page_start, snippet[:80]) — same chunk = one chip
    """
    kind = c.get("kind") or "file"
    if kind == "web":
        return ("web", c.get("url") or c.get("uri"))
    return (
        "file",
        c.get("uri"),
        c.get("page_start"),
        (c.get("snippet") or "")[:80],
    )


def dedupe_and_renumber(raw: list[dict]) -> tuple[list[dict], dict[int, int]]:
    seen: dict[tuple, int] = {}
    final: list[dict] = []
    remap: dict[int, int] = {}
    for c in raw:
        key = _dedupe_key(c)
        if key in seen:
            remap[c["idx"]] = seen[key]
            continue
        new_idx = len(final) + 1
        seen[key] = new_idx
        remap[c["idx"]] = new_idx
        final.append({**c, "idx": new_idx})
    return final, remap


def rewrite_refs(text: str, remap: dict[int, int]) -> str:
    return _REF_RE.sub(
        lambda m: f"[{remap.get(int(m.group(1)), m.group(1))}]", text
    )
