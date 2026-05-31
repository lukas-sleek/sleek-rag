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
# German-style multi-cite: `[1, 2]`, `[1,2,3]`, `[1 , 2]`. Models tend to
# emit this form even when the prompt asks for `[1][2]`. Without expansion,
# the single-ref regex below skips it and rewrite_refs leaves the in-text
# markers untouched while dedupe collapses the chips below — so the prose
# references chip [2] that doesn't exist in the chip strip.
_MULTI_REF_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)+)\]")


def _dedupe_key(c: dict) -> tuple:
    """Per-record dedupe key. Web vs file collapse independently:
    - web: (kind, url) — same URL cited twice = one chip.
    - file: (kind, file_id) — all chunks of the same file collapse to
      one chip in the citation footer, but each chunk's snippet + score
      is preserved on the surviving record's `chunks` array (see
      `dedupe_and_renumber`). The hover renders that array so the user
      can see WHICH passages grounded the answer, with their individual
      confidences. When Vertex starts populating page spans we'll switch
      back to per-chunk dedupe so each [N] points to a specific page.
    """
    kind = c.get("kind") or "file"
    if kind == "web":
        return ("web", c.get("url") or c.get("uri"))
    return ("file", c.get("file_id"))


def _chunk_view(c: dict) -> dict:
    """Project a citation record down to the per-chunk fields the hover
    needs. Used to populate the `chunks` array on the surviving file-level
    citation."""
    return {
        "chunk_id": c.get("chunk_id"),
        "snippet": c.get("snippet"),
        "score": c.get("score"),
        "page_first": c.get("page_first"),
        "page_last": c.get("page_last"),
    }


def dedupe_and_renumber(raw: list[dict]) -> tuple[list[dict], dict[int, int]]:
    seen: dict[tuple, int] = {}
    final: list[dict] = []
    remap: dict[int, int] = {}
    chunk_seen: dict[int, set] = {}  # new_idx -> set of chunk_ids already merged
    for c in raw:
        key = _dedupe_key(c)
        view = _chunk_view(c)
        if key in seen:
            new_idx = seen[key]
            remap[c["idx"]] = new_idx
            # Append this chunk's data to the surviving record so hover
            # can show all distinct passages from this file. De-dup by
            # chunk_id within the array so a multi-question fan-out
            # citing the same passage twice doesn't double-render it.
            cid = view["chunk_id"]
            if cid is None or cid not in chunk_seen[new_idx]:
                final[new_idx - 1].setdefault("chunks", []).append(view)
                if cid is not None:
                    chunk_seen[new_idx].add(cid)
            continue
        new_idx = len(final) + 1
        seen[key] = new_idx
        remap[c["idx"]] = new_idx
        survivor = {**c, "idx": new_idx, "chunks": [view]}
        final.append(survivor)
        chunk_seen[new_idx] = {view["chunk_id"]} if view["chunk_id"] is not None else set()
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


def _expand_multi_ref(m: re.Match) -> str:
    """`[1, 2, 3]` -> `[1][2][3]`. Lets the existing single-ref pipeline
    apply the remap and adjacent-run collapse to multi-cite forms uniformly."""
    parts = [p.strip() for p in m.group(1).split(",")]
    return "".join(f"[{p}]" for p in parts if p.isdigit())


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
    # First expand `[N, M]` -> `[N][M]` so the rest of the pipeline doesn't
    # have to special-case the comma form.
    expanded = _MULTI_REF_RE.sub(_expand_multi_ref, text)
    renumbered = _REF_RE.sub(
        lambda m: f"[{remap.get(int(m.group(1)), m.group(1))}]", expanded
    )
    return _REF_RUN_RE.sub(lambda m: _dedupe_marker_run(m.group(0)), renumbered)
