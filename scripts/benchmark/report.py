"""Markdown report generator for benchmark runs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable

from scripts.benchmark.judge import winner_for

VARIANT_TITLES = {
    "A": "Vanilla Vertex RAG (= 18.3 target)",
    "B": "Current sleek-rag chat",
    "C": "Current sleek-rag projektanalyse v2",
}

DIMENSIONS = ("accuracy", "completeness", "citation", "conciseness")


def _quote(text: str) -> str:
    text = (text or "").strip() or "_(leer)_"
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def _summary_table(per_question: list[dict], variant_labels: list[str]) -> str:
    header = "| Question | " + " | ".join(f"{l} win" for l in variant_labels) + " | tie |"
    sep = "|" + "---|" * (len(variant_labels) + 2)
    rows: list[str] = [header, sep]

    for entry in per_question:
        judgments = entry.get("judgments")
        winner = winner_for(judgments) if judgments else "—"
        cells = []
        for label in variant_labels:
            cells.append("✓" if winner == label else "")
        cells.append("✓" if winner == "tie" else "")
        title = entry["id"]
        rows.append(f"| {title} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _aggregate_table(
    per_question: list[dict], variant_labels: list[str]
) -> str:
    n = sum(1 for e in per_question if e.get("judgments"))
    if n == 0:
        return "_(no judge scores — set --judge to enable)_"

    totals: dict[str, dict[str, int]] = {
        label: {dim: 0 for dim in DIMENSIONS} for label in variant_labels
    }
    for entry in per_question:
        judgments = entry.get("judgments") or {}
        for label in variant_labels:
            j = judgments.get(label)
            if not j:
                continue
            for dim in DIMENSIONS:
                totals[label][dim] += j["scores"][dim]

    max_per_dim = 3 * n
    header = "| Dim | " + " | ".join(variant_labels) + " |"
    sep = "|" + "---|" * (len(variant_labels) + 1)
    rows = [header, sep]
    for dim in DIMENSIONS:
        cells = [
            f"{totals[label][dim]}/{max_per_dim}"
            for label in variant_labels
        ]
        rows.append(f"| {dim} | " + " | ".join(cells) + " |")

    grand: list[str] = []
    for label in variant_labels:
        s = sum(totals[label].values())
        grand.append(f"{s}/{4 * max_per_dim}")
    rows.append(f"| **total** | " + " | ".join(grand) + " |")
    return "\n".join(rows)


def _judgment_line(judgments: dict[str, dict] | None, variant_labels: list[str]) -> str:
    if not judgments:
        return "_(no judge run)_"
    bits = []
    for label in variant_labels:
        j = judgments.get(label)
        if not j:
            bits.append(f"{label}=—")
            continue
        s = j["scores"]
        bits.append(
            f"{label}={s['accuracy']}/{s['completeness']}/{s['citation']}/{s['conciseness']}"
        )
    winner = winner_for(judgments)
    line = ", ".join(bits)
    rationales = []
    for label in variant_labels:
        j = judgments.get(label)
        if j and j.get("rationale"):
            rationales.append(f"  - **{label}**: {j['rationale'].strip()}")
    rat_block = "\n".join(rationales)
    return f"**Judge:** {line} — Winner: **{winner}**\n\n{rat_block}"


def generate_report(
    *,
    per_question: list[dict],
    variant_labels: list[str],
    output_dir: str | Path,
    timestamp: str | None = None,
) -> Path:
    """per_question entries: {id, question, variants: {A: {...}, B: {...}, C: {...}}, judgments?}."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = timestamp or datetime.now().strftime("%Y%m%dT%H%M%S")
    path = output_dir / f"{ts}-report.md"

    lines: list[str] = []
    lines.append(f"# Benchmark report — {ts}")
    lines.append("")
    lines.append("## Setup")
    for label in variant_labels:
        lines.append(f"- **Variant {label}**: {VARIANT_TITLES.get(label, label)}")
    lines.append(f"- Question set: {len(per_question)} questions")
    lines.append("")
    lines.append("## Summary")
    lines.append(_summary_table(per_question, variant_labels))
    lines.append("")
    lines.append("## Aggregate scores (judge)")
    lines.append(_aggregate_table(per_question, variant_labels))
    lines.append("")
    lines.append("## Per-question detail")
    lines.append("")

    for i, entry in enumerate(per_question, start=1):
        lines.append(f"### Q{i} ({entry['id']}): {entry['question']}")
        lines.append("")
        for label in variant_labels:
            payload = entry["variants"].get(label) or {}
            answer = payload.get("answer", "")
            latency = payload.get("latency_s") or payload.get("latency_s_total")
            extras = []
            if latency is not None:
                extras.append(f"latency={latency}s")
            grounding_uris = payload.get("grounding_uris")
            if grounding_uris is not None:
                extras.append(f"grounding={len(grounding_uris)} chunk(s)")
            citations = payload.get("citations")
            if citations is not None:
                extras.append(f"citations={len(citations)}")
            extras_s = " · ".join(extras)
            lines.append(
                f"**{label} — {VARIANT_TITLES.get(label, label)}** {('· ' + extras_s) if extras_s else ''}"
            )
            lines.append("")
            lines.append(_quote(answer))
            lines.append("")
        lines.append(_judgment_line(entry.get("judgments"), variant_labels))
        lines.append("")
        lines.append("---")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
