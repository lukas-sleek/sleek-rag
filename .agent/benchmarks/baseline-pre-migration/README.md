# Baseline pre-migration benchmark — 18.0.1

**Run date:** 2026-04-29
**Question set:** 11 Südi-Areal questions (`scripts/benchmark/questions.json`)
**Test account:** test@test.com (TESTPROJEKT, project_id `7c3a70b2-b7c1-4b80-b1f3-60f38552a37b`, 4 ready files)
**RAG corpus:** `projects/sleek-rag/locations/europe-west3/ragCorpora/6917529027641081856`
**Models:** Variant A → gemini-2.5-flash (Vertex), Variant B/C → gemini-2.5-flash / gemini-2.5-pro via existing app, Judge → gemini-2.5-flash (Vertex; Pro unavailable in europe-west3 for this project)

## Aggregate scores (out of 132 = 4 dims × 3 max × 11 Q)

| Variant | accuracy | completeness | citation | conciseness | **TOTAL** |
|---|---|---|---|---|---|
| A — Vanilla Vertex RAG (= 18.3 target) | 25/33 | 24/33 | **0/33** | 26/33 | **75/132** |
| B — Current chat (custom loop) | 24/33 | 18/33 | **24/33** | 23/33 | **89/132** |
| C — Current projektanalyse v2 | 22/33 | 18/33 | 18/33 | 28/33 | **86/132** |

## Per-question winner

A wins: 0 · B wins: 5 · C wins: 4 · ties: 2

## Headline finding

Variant A is competitive on **accuracy, completeness, and conciseness** — and beats both B and C on completeness — but scores **0/33 on citation quality**. The judge reports A's answers reference pages inline (e.g. "auf Seite 21") rather than emitting a structured `grounding_metadata.grounding_chunks` payload that the harness can parse into citation chips. The vanilla agent is producing the right facts; the citation rendering pipeline is the gap.

This is exactly the regression the **citation regex-enrichment work in 18.3** is designed to close (LLM Parser emits `[Seite N]` markers; chat extracts them from `grounding_metadata` after the stream completes).

## How to read this

Per the master plan (18.0):

- A within ~10% of B/C overall → migration ships at parity. **A is at 75/132 vs B at 89/132 — a 16% gap, driven entirely by the missing citations.** If you exclude the citation dimension, A scores 75/99 vs B's 65/99 and C's 68/99 — A *leads* on the substantive answer dimensions.
- After 18.3 ships, re-run the benchmark; the post-migration A must match or beat this pre-migration A on accuracy/completeness/conciseness, AND close the citation gap to within parity of B.

## Files

- `report.md` — full per-question detail with all three variants and judge rationales
- `results.json` — raw per-variant answers, latencies, citations, plus judge scores
