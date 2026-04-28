"""Heading-path extraction for Document AI Layout Parser chunks.

Two extraction paths run on every chunk:

- Path A (clean case): peel consecutive markdown heading lines from the top
  of `chunk.content`. These are the ancestor headings Document AI prepends
  when `include_ancestor_headings=True` is set on the layout config.
- Path B (recovery case): scan the body for inline section markers like
  "1.3 FRAGEN" — the cases where Layout Parser failed to detect a heading
  and left the marker as run-on paragraph text.

Pure-Python, no I/O. The Postgres equivalent in migration 0011 mirrors this
behavior for the one-time backfill.
"""
from __future__ import annotations

import re

_HEADING_LINE_RE = re.compile(r"^#+\s+(.+?)\s*$")

_INLINE_SECTION_RE = re.compile(
    r"(?:^|(?<=\D))"                            # start-of-line OR preceded by non-digit
    r"(\d+(?:\.\d+)+)"                          # numeric section with ≥1 dot (1.3, 3.4.2)
    r"\s+"
    r"([A-ZÄÖÜ][A-ZÄÖÜ /-]{1,127}[A-ZÄÖÜ])"    # uppercase title, ≥3 chars, ends in uppercase
    r"(?=[A-ZÄÖÜ][a-zäöüß]|\W|$)",              # followed by camelCase boundary OR non-word OR EOL
    re.MULTILINE,
)


def extract_heading_path(content: str | None) -> list[str] | None:
    """Pull the chapter hierarchy from a Document AI Layout Parser chunk.

    Returns a flat list ordered shallow → deep, or `None` if nothing usable
    is found. The column on `document_chunks` is nullable; preserve that
    semantic for chunks with no recoverable structure.
    """
    if not content or not content.strip():
        return None

    result: list[str] = []
    lines = content.split("\n")
    body_start = 0

    # Path A: peel consecutive markdown heading lines from the top, skipping
    # blank lines between them. Document AI prepends ancestors as
    # "# H1\n\n## H2\n\n### H3\n\n<body>" — the blanks must not terminate.
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = _HEADING_LINE_RE.match(line)
        if not m:
            break
        text = m.group(1).strip()
        if text:
            result.append(text)
        i += 1
    body_start = i

    body = "\n".join(lines[body_start:])

    # Path B: scan body for inline section markers.
    seen = set(result)
    for num, title in _INLINE_SECTION_RE.findall(body):
        entry = f"{num} {title.strip()}"
        if entry not in seen:
            seen.add(entry)
            result.append(entry)

    return result or None
