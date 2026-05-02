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
    - file: (kind, file_id) — all chunks from the same file collapse to
      one chip. Rationale: the user-facing citation list shows filenames,
      not chunk excerpts; surfacing 7 numbered chips that all read
      'HO_Teil_B...pdf' just because Vertex returned 7 chunks from that
      file is misleading. Chunk-level snippets stay accessible via the
      activity panel's `Treffer` rendering — the citation footer shows
      one entry per cited file.
    """
    kind = c.get("kind") or "file"
    if kind == "web":
        return ("web", c.get("url") or c.get("uri"))
    return (
        "file",
        c.get("file_id"),
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


_REF_RUN_RE = re.compile(r"(?:\[\d+\]){2,}")


def _dedupe_marker_run(run: str) -> str:
    """Collapse a run of adjacent [N] markers, keeping each idx once and in
    order of first appearance. `[2][2]` -> `[2]`, `[1][2][1]` -> `[1][2]`."""
    seen: list[str] = []
    for m in _REF_RE.finditer(run):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return "".join(f"[{n}]" for n in seen)


def rewrite_refs(text: str, remap: dict[int, int]) -> str:
    """Apply the dedupe-and-renumber remap to all `[N]` markers in `text`,
    then collapse adjacent duplicate markers (e.g. `[2][2]` -> `[2]`).

    Adjacent duplicates arise on two paths: (1) the same source dedupes to
    the same global idx after renumbering (e.g. two rag_specialist sub-calls
    cite chunks that collapse to one canonical entry), and (2) the
    orchestrator concatenates adjacent claims that happen to share a
    source. Either way the duplicate marker is pure noise — the underlying
    chip is the same — so we drop it before persisting / streaming the
    annotated answer."""
    renumbered = _REF_RE.sub(
        lambda m: f"[{remap.get(int(m.group(1)), m.group(1))}]", text
    )
    return _REF_RUN_RE.sub(lambda m: _dedupe_marker_run(m.group(0)), renumbered)
