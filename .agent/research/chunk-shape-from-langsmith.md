# Production chunk shape — pulled from LangSmith

**Source**: LangSmith project `sleek-rag` (https://eu.api.smith.langchain.com), 800 most recent runs (~24h window covering 2026-04-28 19:55 → 2026-04-29 19:37 UTC).

**Production tenant**: project_id `7c3a70b2-b7c1-4b80-b1f3-60f38552a37b` (Hochdorf Südiareal Infrastruktur tender). All non-test traces in the window come from this single project — 4 indexed files, all PDFs (one converted from .docx, one from .xlsx).

---

## 1. Trace topology

Run-name distribution across the 800-run window:

| Run name                          | Count | Run type | Notes |
|-----------------------------------|------:|----------|-------|
| `ChatOpenAI`                      |   158 | llm      | OpenAI-compat client wrapper, auto-instrumented |
| `chats.send_message`              |   123 | chain    | Top-level chat handler |
| `search_chunks`                   |   113 | tool     | Supabase `match_chunks_hybrid` RPC tool |
| `sufficiency_check`               |    90 | llm      | DELETED in plan 18.3 T1 — only present in pre-rewrite history |
| `rank`                            |    81 | tool     | Qwen3-Reranker over candidate chunks |
| `answer_verifier`                 |    67 | llm      | DELETED in plan 18.3 T1 |
| `read_section`                    |    47 | tool     | DELETED in plan 18.3 T2 |
| `files.upload`                    |    44 | chain    | Ingestion entry |
| `list_document_outline`           |    38 | tool     | DELETED in plan 18.3 T3 |
| `expand_query`                    |    16 | llm      | Query expander before search_chunks |
| `projektanalyse_v2.answer_one`    |    13 | llm      | One per template question |
| `documentai.layout_parser`        |     4 | retriever | Per-file ingestion span |
| `ingest.process_job`              |     4 | chain    | Per-file ingestion top |
| `projektanalyse.run`              |     2 | chain    | Wraps a batch of `*.answer_one` siblings |

### Two distinct trace shapes coexist in this window

**(a) Pattern A grounding (current, post-18.3, last ~30 traces) — flat single-node trace.**
The recent rewrite uses Vertex RAG via the SDK's `tools=[grounding_tool]`, so retrieval happens inside the model call and emits no LangSmith children. Example trace 019ddabf has exactly 1 run.

```
chain  chats.send_message        (root)
```

**(b) Old tool-loop chat (pre-18.3, traces from 2026-04-29 ~12:25 UTC and earlier) — multi-step.**
The model called `search_chunks`, optionally `read_section`, then `sufficiency_check` and `answer_verifier` LLM probes. Trace 019dd933:

```
chain  chats.send_message
  tool  search_chunks
  tool  search_chunks
    tool  rank                       (child of search_chunks)
  llm   sufficiency_check
    llm   ChatOpenAI                 (child of sufficiency_check)
  tool  list_document_outline
  tool  read_section
  llm   answer_verifier
```

**(c) Projektanalyse — one big trace per "run all template questions".**
`projektanalyse.run` is a sibling of the root `chats.send_message` (parent = the chat run). Below it sits one `projektanalyse_v2.answer_one` LLM run per template question (11 in the sampled trace), each answer_one wrapping its own `ChatOpenAI` SDK call. Trace 019dd8d6 example:

```
chain  chats.send_message
  chain  projektanalyse.run
    llm  projektanalyse_v2.answer_one × 11
      llm  ChatOpenAI × 1 each
```

Note: `projektanalyse.answer_one` (v1, no `_v2` suffix) does **not appear** in the 24h window — only `projektanalyse_v2.answer_one`. The v1 path is dormant.

### What the trace actually captures for chunks

For Pattern A `chats.send_message`, **the raw `grounding_chunks[*].retrieved_context.text` body is NOT in the LangSmith trace**. The traceable decorator only sees the function's args and return value, and the SDK chunk objects never get serialized as inputs. What we *do* get is `outputs.output` — the SSE stream the function yielded. Inside that stream is a `meta` frame whose `citations[]` array contains a derived shape with `snippet` truncated to 200 chars (see `backend/app/citations.py:79`). For older traces, `search_chunks` outputs include `excerpt` truncated to 281 chars. Below I quote those snippets verbatim — they are the closest thing to ground-truth chunk text that LangSmith has.

---

## 2. Three verbatim chunk samples from `chats.send_message`

All from Pattern A grounded answers in run trace `019ddabf` ("und was ist mit etappe 1?"). `snippet` is the first 200 chars of `retrieved_context.text` after `.strip()`.

### Sample 2A — clean page header at top of chunk
```
filename:    HO_Teil_B_Südi-Areal-Infrastruktur_def.pdf
page_start:  14
page_end:    14
figure_label: Abb. 9       (LATER in chunk, not in first 200 chars)
snippet (200/200 chars):
[Seite 14]
Gemeinde Hochdorf, Entwicklung Südiareal, Infrastruktur Gesamtplanerbeschaffung-Dokument B

# 3 ETAPPIERUNG UND GROBKOSTENSCHÄTZUNG

## 3.1 ETAPPIERUNG
Gemäss Erschliessungsrichtplan wird f
```

### Sample 2B — page marker mid-chunk (after a running header)
```
filename:    HO_Teil_B_Südi-Areal-Infrastruktur_def.pdf
page_start:  3
page_end:    3
figure_label: None
snippet (200/200 chars):
Gemeinde Hochdorf, Entwicklung Südiareal, Infrastruktur Gesamtplanerbeschaffung-Dokument B

[Seite 3]
# 1 AUSGANGSLAGE
Die Gemeinde Hochdorf hat das Südiareal im Jahre 2021 von der Hochdorf-Gruppe erw
```
Note: `[Seite 3]` is at char position 92, not 0. The Layout Parser placed the document running-header *before* the page marker. The `_PAGE_RE` regex still finds it.

### Sample 2C — figure block with `[Abb.]` and `[Inhalt:]` markers
```
filename:    HO_Teil_B_Südi-Areal-Infrastruktur_def.pdf
page_start:  15
page_end:    15
figure_label: Abb. 10
snippet (200/200 chars):
[Seite 15]
# Gemeinde Hochdorf, Entwicklung Südiareal, Infrastruktur Gesamtplanerbeschaffung-Dokument B

[Abb. 10: Infrastruktur-Massnahmen der Etappen 1 und 2.]
[Inhalt: Eine Karte des Südiareal Hoch
```

### Sample 2D — table-row chunk WITHOUT page marker (bonus, illustrates failure mode)
```
filename:    HO_Teil_B_Südi-Areal-Infrastruktur_def.pdf
page_start:  None      <-- no [Seite N] match in first 200 chars
page_end:    None
snippet (200/200 chars):
Elektro, Wärme/Gas) | 3 | 205'000 | 6'537'000 | | X |
| 4a | Realisierung definitve Wendeschlaufe Bushof West | 4 | 2'080'000 | | X | |
| 4b | Bushof West Etappe 2 / Ersatzverkehr | 4 | 1'130'000 | | 
```

---

## 3. Chunk-shape findings — Pattern A (Vertex RAG)

Sampled across the **last 36 unique chunks** returned to `chats.send_message` in 12 recent runs (snippet column = first 200 chars of `retrieved_context.text`).

| Property                                       | Count | Pct  |
|------------------------------------------------|-------|------|
| Chunks with at least one `[Seite N]` marker    | 32/36 | 89%  |
| Chunks with NO `[Seite N]` in first 200 chars  |  4/36 | 11%  |
| Chunks with TWO+ distinct `[Seite N]` values   |  0/36 |  0%  |
| Chunks with `[Abb. <N>: ...]` figure marker    |  6/36 | 17%  |
| Chunks with `[Inhalt: ...]` (figure caption)   |  5/36 | 14%  |
| Chunks containing markdown-table rows (`|...|`)|  5/36 | 14%  |

**Position of the page marker** (char offset within the first 200 chars where the first `[Seite N]` occurs across the 32 chunks that have one): mostly 0 (the marker is the first thing in the chunk), with 3 cases at offsets 91/92/92 — these are chunks where the Layout Parser placed a running header line **before** the page marker (see Sample 2B).

### Direct answers to the asked questions

- **Are pages emitted as `[Seite N]` markers inline?** Yes, in 89% of chunks. The Layout Parser prompt does inject them, and the regex `\[Seite\s+(\d+)\]` in `citations.py:22` finds them. **But it is not 100% reliable** — table-heavy chunks and continuation chunks across page boundaries can have no marker at all.
- **Are figures emitted as `[Abb. N: ...]`?** Yes, when the chunk contains a figure. Format is exactly `[Abb. <N>: <caption>]` followed by `[Inhalt: <description>]`. The figure-label regex `_FIGURE_RE` matches.
- **How many `[Seite N]` markers per chunk?** **At most one in the visible 200 chars** (max=1, mean=0.89). Zero chunks span a page boundary in the snippets we can see. The metadata pages reported in `match_chunks_hybrid` results (Section 5 below) show that the underlying chunks DO sometimes span 2-4 pages, but the Vertex-emitted chunk text shows only a single inline `[Seite N]` per chunk in the prefix we have visibility into.
- **Other structural features.** Markdown headers (`#`, `##`, `###`) appear in nearly every chunk. Running headers ("Gemeinde Hochdorf, Entwicklung Südiareal, ...") are preserved verbatim, sometimes before the `[Seite N]` marker. Table rows render as pipe-delimited markdown. Bold uses `**...**`. Bullet lists use `*` prefix.

---

## 4. `grounding_supports` granularity

`grounding_supports` are not stored in the trace as raw byte offsets — but the function in `citations.py:167-186` (`annotate_answer_with_refs`) consumes them and splices `[N]` markers into the answer text, which IS in the SSE output as `meta.content`. Reverse-engineering from there:

Across 8 recent send_message runs:

| run id    | answer chars | n_citations | n_refs | mean span len between refs | min   | max   |
|-----------|-------------:|------------:|-------:|---------------------------:|------:|------:|
| 019ddabf  |          514 |           5 |      2 |                        171 |   110 |   278 |
| 019ddabe  |          241 |           5 |      2 |                        120 |    56 |   185 |
| 019ddabe  |          369 |           5 |      3 |                        123 |    29 |   256 |
| 019ddabd  |          425 |           5 |      4 |                        106 |    85 |   149 |
| 019ddabd  |           60 |           5 |      0 |                          — |     — |     — |
| 019ddabb  |           68 |           5 |      0 |                          — |     — |     — |

**Granularity is sentence-to-clause level**, not phrase. Span lengths between consecutive `[N]` markers cluster at 80–170 chars, which is roughly one Markdown bullet line or one German sentence. Refs land at the END of grounded spans (sentence-final position).

**Significant caveat: 2/8 runs returned 5 retrieved chunks but ZERO `grounding_supports`.** Both were short answers (60 / 68 chars) — Vertex returned the chunks as evidence but did not pin any answer span to them. In those cases citations show on the side but inline `[N]` markers do not appear in the answer.

### Two grounding_supports examples

#### Example 4A — bullet list with refs at line-end (run 019ddabe, "Was ist die Bausumme?")

```
Der Gesamt-/Headline-Wert für die gesamte Bausumme des Projekts ist in den
Dokumenten nicht explizit als eine einzelne Gesamtsumme enthalten. Die
Grobkostenschätzung der Baukosten pro Etappe (Angaben exkl. MwSt.) ist wie
folgt:

*   Etappe 1: 1'935'000 CHF[1]
*   Etappe 2: 16'381'000 CHF[1]
*   Etappe 3: 6'537'000 CHF
*   Etappe 4: 4'638'000 CHF
*   Etappe 5: 8'680'000 CHF[2]
```

`[1]` references chunk index 0 = `HO_Teil_B...pdf p=17` (the cost overview page). `[2]` references chunk index 1 = `HO_Teil_B...pdf` page-None (a table-row chunk with no page marker — see Sample 2D). Etappes 3 and 4 have no support — they came from the same chunk[0] as etappe 1+2 but Vertex chose not to emit a separate support entry per bullet.

#### Example 4B — paragraph-level refs (run 019ddabf, "und was ist mit etappe 1?")

```
Die Etappe 1 umfasst die Realisierung Bushof Ost inklusive Zufahrt, die
Rampe Fussweg zwischen Siedereistrasse & Lavendelweg, die Optimierung PU
Nord und die Ergänzung Radweg Nord. Die Umnutzung des bestehenden Areals
für die Etappe 1 ist für die Jahre 2026 bis 2028 vorgesehen.[1] Die
Testplanung für das gesamte Südiareal wurde Ende 2023 abgeschlossen und
bildet die Grundlage für die weitere Entwicklung.[2] Ein "Dorfplatz" wird
im Kontext der Massnahmen der Etappe 1 in den bereitgestellten Dokumenten
nicht genannt.
```

`[1]` covers ~278 chars (two sentences) → chunk 0 (`p=14`, Sample 2A). `[2]` covers ~110 chars (one sentence) → chunk 1 (`p=3`, Sample 2B). The closing "nicht genannt" sentence has no support — Vertex didn't emit one because no chunk supports a negative claim.

---

## 5. `projektanalyse_v2.answer_one` chunks (Section was asked about v1; only v2 is live)

⚠️ **There are no `projektanalyse.answer_one` (v1) runs in the 24h window** — only `projektanalyse_v2.answer_one`. The v1 code path may exist in `backend/app/projektanalyse.py:214` but is not invoked in production traffic right now.

⚠️ **v2 does not use `match_chunks_hybrid` RPC.** It does not retrieve at all. Looking at v2 inputs:

```python
inputs.keys() == ['question', 'corpus']
```

The `corpus` is **the full text of every indexed document in the project**, concatenated. Sample 019dd8d6-35e6: `corpus` is **155,750 characters long** (155 KB), containing all 4 project files separated by:

```
=== HO_Teil_C1_Südi-Areal-Infrastruktur_def_Word.docx ===
...
=== HO_Teil_C2_Südi-Areal-Infrastruktur_def_Excel.pdf ===
...
=== HO_Teil_A_Südi-Areal-Infrastruktur_def.pdf ===
...
=== HO_Teil_B_Südi-Areal-Infrastruktur_def.pdf ===
...
```

### Format of pages and figures inside the v2 corpus

This is a **completely different ingestion artifact** from what Vertex RAG sees:

- **Page markers are `[S.<N>]`, not `[Seite <N>]`.** 69 occurrences, 24 unique pages in the sampled corpus. Zero `[Seite N]` markers anywhere.
- **No `[Abb. N: ...]` markers.** Zero matches. Figures use the wrapper:
  ```
  __START_OF_ANNOTATION__The image displays a form titled "Referenzobjekt
  Gesamtplanung / -koordination" (Reference Project General Planning /
  -coordination)... [long English description of the image] ...__END_OF_ANNOTATION__
  ```
  Image annotations are full English-language descriptions written by the LLM annotator, not the German `[Inhalt: ...]` callouts that Vertex chunks contain.
- **Multiple pages per "chunk" — actually one corpus = whole project.** v2 does no chunking. The LLM sees every page in sequence.

### Verbatim slice from a v2 corpus (chars 9000–10500 of run 019dd8d6-35e6)

```
...tion request for bidding communities or subcontractors, perhaps related
to their operational standards or qualifications.__END_OF_ANNOTATION__

[S.4]
# 1.3 ZUSÄTZLICHE ANGABEN BEI BIETERGEMEINSCHAFTEN / SUBUNTERNEHMERN

## Haftpflichtversicherung

|-|-|
| Die Firma hat folgende Haftpflichtversicherung abgeschlossen: |  |
| Versicherungsgesellschaft |  |
| Deckungssumme CHF | Selbstbehalt CHF |
| Personenschäden |  |
| Sachschäden |  |
| Bauten- / Anlage- / Vermögens- schäden |  |

Die Firma bestätigt mit Eingabe des Angebotes, dass sie im Auftragsfall
einen Nachweis des Q- Managements und den Versicherungsnachweis dem
Auftraggeber vorweisen kann.

[S.5]
# 2 FIRMENREFERENZEN FÜR DEN NACHWEISE DER LEISTUNGSFÄHIGKEIT DES ANBIETERS
(EIGNUNGSKRITERIUM EK 1)
```

### Bonus: the `search_chunks` (Supabase hybrid) chunk shape — third format

Older `chats.send_message` traces (pre-18.3 rewrite) contain `search_chunks` tool calls with rich outputs. Project `7c3a70b2...` (real prod), 69 traced calls. Sample (run 019dd933-d83f, "Vermessung Auftrag Leistungsumfang"):

```json
{
  "block_type": "paragraph",
  "chunk_id": "...",
  "excerpt": "# 7 PROJEKTABLAUF FÜR PHASE «VORPROJEKT PLUS»\n\n## 8 LEISTUNGSBESCHRIEB\n\n### 8.1 ART DER LEISTUNG\n\nDer Anbieter erbringt die Grundleistungen gemäss SIA 103 Planer als Gesamtleiter für die Phase 21 und 31. Ausserdem sind die Vorgaben der SN 640 210 „Entwurf des Strassenraumes; Vorg…",
  "filename": "HO_Teil_B_Südi-Areal-Infrastruktur_def.pdf",
  "page_start": 23,
  "page_end": 26,
  "figure_label": null,
  "ref": 1,
  "similarity": 0.1741
}
```

In this format (Supabase chunks):
- **NO `[Seite N]` inline markers** — page numbers are metadata fields.
- **Multi-page chunks are common**: `page=4-5`, `page=18-21`, `page=23-26` all appear. The chunker happily spans page boundaries.
- Markdown headers preserved.
- `similarity` scores are LOW (0.04–0.17 range), reflecting the German embedding distribution.
- Excerpt truncated to 281 chars.

---

## 6. Implications for the planned ADK migration

### Three different chunk-text contracts exist in production right now

| Path                              | Page marker          | Figure marker                              | Spans pages? | Chunk size              |
|-----------------------------------|----------------------|--------------------------------------------|--------------|-------------------------|
| Vertex RAG (current chat)         | `[Seite N]` inline   | `[Abb. N: <cap>]` + `[Inhalt: <desc>]`     | Rarely       | Auto, ~500-1500 chars   |
| Supabase `match_chunks_hybrid`    | metadata only        | metadata only                              | Yes          | Variable, multi-page    |
| projektanalyse_v2 corpus          | `[S.N]` inline       | `__START_OF_ANNOTATION__...__END__`        | N/A (whole-doc) | 150 KB / project     |

These come from three different ingestion artifacts. The Vertex RAG-fed chunks are produced by a Layout Parser prompt that injects `[Seite N]` and `[Abb. N: ...]` (plan 18.2 T2). The Supabase chunks have stripped those markers (or never had them) and rely on metadata fields. The v2 corpus is yet a third format.

### Does the `[Seite N]` regex contract hold?

**Mostly, but not strictly.** Verdict: **89% reliable**, fails predictably on:

1. **Table-heavy chunks** that start mid-row (e.g. Sample 2D — pure pipe-delimited continuation of a cost table). When the parser splits a long table across chunks, the continuation chunk has no header.
2. **Body-text continuation chunks** that begin mid-paragraph (e.g. "Der umgestaltete **Bahnhofsplatz** und der **Bushof Ost**...") with no preceding page marker.
3. **Body chunks where the running header precedes the page marker** (Sample 2B). The regex still matches, but a naive "first 100 chars" check would miss this.

Across 36 retrieved chunks: **4 (~11%) have `page_start = None`** in the citation payload because the regex didn't fire. The downstream UI handles this — `page_start` is nullable in `Citation` (the recent commit 825a4e3 fixed it explicitly: "fix(ui): handle nullable page numbers in citation chip"). Confirmed live.

### Concrete recommendations for the ADK migration

1. **Don't rely on `[Seite N]` as a strict invariant** in any new code. Treat it as a soft signal that fires on ~9 out of 10 chunks. Fall back to chunk-level metadata (Vertex `retrieved_context.uri` and `title`) for filename, and accept `page = None` for tables and mid-paragraph continuation.
2. **Pick ONE chunk format and retire the other two before migrating.** Today, depending on which entrypoint a question hits (chat vs. projektanalyse-template-batch), the model sees radically different document representations. ADK should standardize. Recommendation: keep the Vertex-style markers (`[Seite N]` + `[Abb.]` + `[Inhalt:]`) since that's what the Layout Parser already produces and what the live chat path uses.
3. **Stop dumping the entire 150 KB project corpus to projektanalyse_v2.** That LLM call sees 4 full documents per template-question, which is wasteful and is also why v2 emits a different page-marker dialect (the .docx/.xlsx ingestion path uses `[S.N]` while the PDF Layout Parser uses `[Seite N]`). Either unify on the Layout Parser format or have ADK retrieve per-question.
4. **`grounding_supports` are sentence-to-clause level (~100-170 chars between refs)** with a real failure rate of ~25% (2/8 short answers had zero supports despite having retrieved chunks). The ADK migration needs an explicit fallback for "model returned chunks but no supports" — the current code silently emits citations without inline `[N]` markers.
5. **The flat trace in Pattern A is a regression for observability.** With grounding moved inside the model call, LangSmith no longer sees the retrieved chunk text — the only thing in the trace is the SSE output with 200-char snippets. If ADK plans to keep Vertex grounding, instrument an explicit retrieval span that captures full `retrieved_context.text` so we can audit chunk quality going forward.
6. **The `projektanalyse.answer_one` (v1) run name is dead in prod.** All template-question traffic uses `projektanalyse_v2.answer_one`. Either delete the v1 traceable, or reactivate it consciously — it should not be left as a vestigial code path the migration carries forward.
