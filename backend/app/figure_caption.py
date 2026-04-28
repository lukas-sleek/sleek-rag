"""Figure label / caption extraction from Document AI chunk content.

Document AI's Layout Parser puts the source-document figure caption (e.g.
"Abbildung 1: Darstellung des Projektperimeters") inside the chunk content
— often at the END, after the LLM-generated visual description. The old
regex was anchored at start-of-text and missed every real caption.

`extract_figure_label` returns a short id ("Abbildung 1") used as the search
key in `match_chunks_filtered`. `extract_figure_caption` returns the full
caption including the descriptive title for display.
"""
from __future__ import annotations

import re

_FIGURE_RE = re.compile(
    r"\b(Figure|Abbildung|Fig\.|Abb\.)\s*([\d.]+)(?:\s*[:.]?\s*([^\n]+))?",
    re.IGNORECASE,
)


def _normalize_kind(raw: str) -> str:
    return raw.rstrip(".").title()


def extract_figure_label(text: str | None) -> str | None:
    """Short label like "Abbildung 1" — stable filter/search key."""
    if not text:
        return None
    m = _FIGURE_RE.search(text)
    if not m:
        return None
    return f"{_normalize_kind(m.group(1))} {m.group(2)}"


def extract_figure_caption(text: str | None) -> str | None:
    """Full caption like "Abbildung 1: Darstellung des Projektperimeters".

    Falls back to the bare label when the source has no descriptive title.
    """
    if not text:
        return None
    m = _FIGURE_RE.search(text)
    if not m:
        return None
    label = f"{_normalize_kind(m.group(1))} {m.group(2)}"
    title = (m.group(3) or "").strip()
    if not title:
        return label
    return f"{label}: {title}"
