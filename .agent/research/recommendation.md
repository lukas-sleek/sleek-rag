# Migration Recommendation: OpenAI → Google Cloud RAG

**Date:** 2026-04-27
**Author:** Synthesis from `google-rag-research.md` + `codebase-map.md`
**Audience:** stefan.bugher@sleek.de (Germany / EU)
**Status:** Opinionated. Recommends one path; calls others wrong-for-this-codebase.

---

## TL;DR

**Build path: Document AI Layout Parser + self-hosted retrieval in Supabase pgvector + Gemini 2.5 Flash for generation.**

Skip Vertex AI Search. Skip Vertex AI / Agent Platform RAG Engine. Both managed paths force you to abandon Supabase as the source of truth, double your storage layer, drag in a Spanner-floor cost (~$65/mo idle even at zero traffic), and — in the case of Vertex AI Search — bind you to a regional product that has reported EU rough edges as recently as Apr 2026. The codebase already runs Postgres-with-pgvector underneath Supabase. Use it.

The Google research itself (section 9) lands on the same conclusion. The codebase map confirms it: the current OpenAI integration is so shallowly abstracted (direct `openai_client().responses.create()` calls, no provider interface, history living entirely in OpenAI Conversations) that you are rewriting the chat path *no matter which Google product you pick*. Given that you're rewriting anyway, take the rewrite that lands on owned infrastructure rather than a second managed-RAG vendor lock-in.

---

## 1. The Three Paths, Compared

| Dimension | A. Vertex AI Search | B. RAG Engine + Layout Parser | **C. Layout Parser + pgvector (DIY)** |
|---|---|---|---|
| **Page citations** | Native (`pageSpan` on chunks via `answer` API with `include_citations: true`) | Indirect — must shove pageSpan into chunk metadata yourself | Native (`pageSpan` from Layout Parser response, store in your own column) |
| **Image retrieval** | Native (image annotation add-on returns bytes + caption per chunk; **Preview** in some SKUs) | Caption-only by default; for bitmap retrieval need parallel Multimodal Embeddings + Vector Search side-index | Native bytes from Layout Parser `returnImages=true`; store in Supabase Storage; reference in chunk row |
| **Realistic 10k pages / 100k qpm cost** | $100 one-time parse + ~$800/mo ($4 search + $4 answer per 1k qps) + storage | $100 parse + ~$65/mo Spanner floor + ~$185/mo Gemini + ~$250/mo grounding ≈ **$500/mo** | $100 parse + ~$0.25 embeddings + ~$185/mo Gemini Flash generation + Supabase storage (incl. existing plan) ≈ **$185–250/mo** |
| **Fit with FastAPI + Supabase + RLS + Realtime** | Poor — your truth becomes the GCP data store; RLS on Supabase becomes redundant; Realtime ingestion updates require glue | Poor — adds Spanner-backed `RagManagedDb` as a second source of truth alongside Supabase | **Excellent** — chunks/images live in Supabase, RLS already enforces tenant isolation, Realtime channel can stream ingestion progress directly |
| **Migration effort vs. current OpenAI coupling** | Medium-low: swap one managed for another. But you abandon the conversation-history-in-OpenAI pattern regardless | Medium: SDK is comfortable but parallel image pipeline is custom anyway | **Medium-high** but linear: parse → chunk row insert → embed → store → query is well-trodden. No surprises. |
| **Lock-in / reversibility** | High — your indexes, parsing, answer generation all live in GCP. Pulling out = full reindex. | High — RagManagedDb chunks aren't portable; corpus exports are limited | **Low** — pgvector is open. Layout Parser is a stateless API. Gemini is one HTTP call you can swap for Claude/OpenRouter later (CLAUDE.md says Module 2+ uses OpenRouter). |
| **EU / GDPR / sleek.de** | Available in `europe-west3` (Frankfurt) but Vertex AI Search's data residency commitment is weaker than RAG Engine's; "data residency and AXT controls **not** supported" per Apr 2026 docs | Available `europe-west3`, `europe-west4` GA, but Apr 2026 forum reports of EU breakage; allowlist-only in some US regions | **Best** — Supabase region you already chose stays the data plane. Only stateless API calls (Document AI, Gemini) go to GCP, and both have EU endpoints. CMEK on Supabase + Document AI in `eu` region keeps content in EU. |
| **Streaming (SSE per CLAUDE.md)** | Yes (dedicated "Stream answers" endpoint) — but citations land in the FINAL chunk, awkward for incremental UI | Yes via `generateContent` streaming | **Yes** — Gemini `generate_content(stream=True)` yields chunks identically to OpenAI; SSE adapter on backend is ~30 LOC change. Citations resolved from your retrieval step *before* streaming starts, so they're available for UI from the first token. |

### Pricing math, shown

**Path C (recommended) at 10k pages, 100k queries/month:**
- Parse: 10k pages × $0.01 = **$100 one-time**
- Embed (text-embedding-005, ~10M tokens corpus): 10 × $0.025 = **$0.25 one-time**
- Embed query (~50 tokens × 100k = 5M tokens/mo): negligible (~$0.13/mo)
- Vector storage: pgvector inside existing Supabase plan = **$0 marginal**
- Gemini 2.5 Flash generation (~2k in / 500 out × 100k): 0.2B in × $0.30/M + 0.05B out × $2.50/M = **$60 + $125 = $185/mo**
- Supabase Storage for extracted images (~10k pages × ~3 figures × ~150KB ≈ 4.5GB): ~**$0.10/mo**
- Total steady-state: **~$185–250/mo + $100 sunk**

**Path A (Vertex AI Search) at the same scale: ~$800/mo.** 4× more for managed convenience you can't extend (e.g. mixing in OpenRouter later, per CLAUDE.md Module 2+ requirement, becomes architecturally awkward).

**Path B (RAG Engine) at the same scale: ~$500/mo + Spanner floor.** Cheaper than Vertex AI Search but uses an opaque vector DB you don't control, and the page-number plumbing is *not* free — you end up writing the same chunk-metadata code as Path C while paying Spanner for the privilege.

---

## 2. Why Path C Wins for *This* Codebase

Three codebase realities decide it:

1. **The chat path is being rewritten regardless.** `backend/app/routers/chats.py:226` is a single `openai_client().responses.create()` call with no abstraction. There is no provider interface to "swap"; the entire endpoint will be re-authored. So the cost of doing it the DIY-pgvector way is *not* "DIY vs. managed" — it's "managed-but-still-rewritten vs. DIY-and-rewritten." The gap shrinks dramatically.

2. **History already needs to move out of OpenAI.** OpenAI Conversations API stores thread history (`chats.openai_thread_id`). Neither RAG Engine nor Vertex AI Search has an equivalent — you must move history into Supabase regardless. This is one of the biggest tasks of the migration, and it's path-agnostic. CLAUDE.md already mandates it for Module 2+ ("stateless completions — store and send chat history yourself"). The migration is the right time to do this once, properly, into Supabase.

3. **RLS is already the security model.** Per `supabase/migrations/0001_init.sql`, every tenant table is gated by `auth.uid() = user_id`. Both managed Google paths force you to layer GCP IAM/ACL on top of GCP-side data, then keep that model in sync with Supabase RLS. Path C keeps a single security model: Postgres row-level security, which the codebase, the test user, and the schema are all built around.

The Google research's pricing analysis and the codebase coupling analysis converge on the same answer. **Confirm — don't challenge — the research's recommendation.** Take Path C.

---

## 3. Blockers & Risks Before Committing

### Codebase changes required regardless of path
1. **Chat history must move from OpenAI Conversations to Supabase.** New tables: `chat_messages(id, chat_id, role, content, citations jsonb, created_at)`. Drop `chats.openai_thread_id`.
2. **Synchronous ingestion polling must become async.** `vs_ingest_file()` blocks the HTTP request today (`files.py:71-119`). Document AI Layout Parser batch is async (poll-based), and 500-page PDFs will absolutely exceed any reasonable HTTP timeout. You need a job queue + Supabase Realtime channel for status updates. CLAUDE.md *already* requires Realtime for ingestion status; current code doesn't comply. The migration is the forcing function to fix it.
3. **Citations need a frontend component.** `chat.tsx` renders plain Markdown with no citation slot. Message type needs `{ role, content, citations: { docId, page, snippet, imageRef? }[] }`. Backend SSE frame needs to carry citation metadata, not just `{ delta }`.
4. **LangSmith wrapping currently assumes OpenAI SDK shape** (`openai_client.py:15`, `wrap_openai`). Replace with explicit LangSmith tracing decorators around the new Gemini and retrieval calls.
5. **Schema additions:**
   - `project_files`: add `gcs_uri` (or Supabase Storage path), drop `openai_file_id`
   - new `document_chunks(id, file_id, page_start, page_end, text, embedding vector(768), metadata jsonb)`
   - new `chunk_images(id, chunk_id, storage_path, caption)`

### Google-side risks (Apr 2026)
1. **Layout Parser image annotation is Preview in some SKUs.** Default API contract is not GA-stable for the bitmap-extraction add-on. Validate in `eu` region before designing the UI around it.
2. **Layout Parser online cap is 15 pages.** All real PDFs will hit batch path, which means async + polling + signed-URL handoff. Build for batch from day one; do not waste effort on the online endpoint.
3. **No volume discount on Layout Parser.** $10/1k pages flat. A 1M-page backfill = $10k. If you anticipate large historical imports, budget separately.
4. **Vertex AI rebrand mid-transition.** "Vertex AI" → "Gemini Enterprise Agent Platform" announced at Cloud Next '26 (Apr 2026). Console paths and SDK names are out of sync. Pin SDK versions; expect doc churn for ~6 months.
5. **Failed-but-partial parses bill for what succeeded.** Wrap Document AI calls with idempotency keys at the page-range level so retries don't double-bill.

### EU / GDPR / sleek.de specific
1. **Document AI is available in `eu` multi-region** — use it. Do not let any document bytes touch a US region. Set `location='eu'` on every Document AI client.
2. **Gemini 2.5 Flash is available in `europe-west3` (Frankfurt) and `europe-west4` (Netherlands)** — pin the generation client there. Avoid `global` endpoints which don't guarantee EU residency.
3. **GDPR DPA:** Google Cloud's DPA covers Document AI and Vertex AI generative APIs as processors. Confirm your customer DPA is signed *before* sending production document bytes; this is a paperwork gate, not an engineering one, but it must be done.
4. **Supabase region:** verify your Supabase project is `eu-central-1` or `eu-west-1`. If it's a US region, that's a separate, larger remediation outside this migration.
5. **Telemetry:** LangSmith is US-hosted (Anthropic-side LangChain). For EU residency hygiene, **scrub or hash document content before logging** — log chunk IDs and metadata, not raw text. This is true regardless of LLM provider, but the migration is a good time to enforce it.

---

## 4. Migration Shape (Phases — High Level Only)

**Phase 1 — Provider abstraction & schema groundwork.** Introduce a thin `RAGProvider` interface in `backend/app/providers/` with two implementations: the existing OpenAI one (kept until Phase 4 cuts over) and a stub Google one. Add the new tables (`chat_messages`, `document_chunks`, `chunk_images`) and the new columns (`gcs_uri` etc.) via a Supabase migration with RLS policies mirroring the existing ones. Wire up Supabase Realtime channel for `project_files` status updates so the frontend can subscribe ahead of any backend rewrite. No behavior changes yet; this is purely structural.

**Phase 2 — Document AI ingestion pipeline.** Replace the synchronous `vs_ingest_file` flow with: upload to Supabase Storage → enqueue an ingestion job → call Document AI Layout Parser (batch, region=`eu`) → for each returned chunk, insert a `document_chunks` row with `pageStart`, `pageEnd`, text, and an embedding from `text-embedding-005` (also `eu`) → for each figure with `returnImages=true`, persist the bytes to Supabase Storage and link via `chunk_images`. Status updates flow through Supabase Realtime. Old OpenAI ingestion path stays alive for projects not yet migrated.

**Phase 3 — Retrieval + chat endpoint rewrite.** Build a retrieval function that takes a query, embeds it, runs cosine-sim over `document_chunks.embedding` filtered by `project_id` (RLS does the rest), returns top-K with their `pageSpan` and any linked images. Replace `openai_client().responses.create()` with a Gemini 2.5 Flash streaming call that takes the retrieved context as system prompt and streams output. SSE frame format extended to carry `{ delta?, citations? }`. Move chat history to `chat_messages`; drop `openai_thread_id` writes (keep the column readable for a deprecation window).

**Phase 4 — Citation + image UI.** Frontend changes to render citations inline (`[doc.pdf p.42]` chip components linking to a viewer) and inline image references for chunks that carry a `chunk_images` row. New file viewer component to show the source PDF page with the cited region highlighted (nice-to-have; minimum is a "open file at page N" link). Cut traffic over project-by-project, deprecate the OpenAI provider implementation, and remove `openai_*` columns in a final migration.

---

## 5. Open Questions Before the Detailed Plan

1. **Image rendering depth.** Do you need inline image rendering in the chat answer (image bytes streamed back, displayed alongside text), or just a "see figure 3 in source.pdf" reference link? This decides whether `chunk_images` persistence is mandatory in Phase 2 or deferrable to Phase 4.

2. **Backfill scope and budget.** How many pages of historical documents need to be re-ingested through Document AI? At $10/1k pages flat with no discount, 100k pages = $1,000, 1M pages = $10,000. Need a number to size the parse budget and decide whether to phase the backfill.

3. **EU residency: hard requirement or strong preference?** If hard (sleek.de B2B customers explicitly requiring EU-only data processing), we pin everything to `europe-west3` / `eu` multi-region and accept the smaller model availability surface. If preference, we get more flexibility on which Gemini SKUs we can use (some are US-first and reach EU later). This also decides LangSmith treatment (scrub vs. allow content).

4. **CLAUDE.md says "Module 2+ uses OpenRouter."** Is the Google migration *replacing* OpenAI for Module 1, or are we additionally repositioning so that Gemini becomes the default but OpenRouter is the swappable Module 2+ generator? This affects whether the `RAGProvider` abstraction in Phase 1 is "Google-only" (simpler) or "Google-for-retrieval, pluggable-for-generation" (more correct per CLAUDE.md but more code).

5. **Multimodal embeddings for purely visual queries.** For technical drawings without legible text labels (blueprints, schematics), Layout Parser's caption-of-figure approach won't help — semantic search hits the caption, not the visual content. Do any of the planned use cases require "find me the drawing that *looks* like this" retrieval? If yes, Phase 2 needs to add a parallel Vertex AI Multimodal Embeddings + side-index pipeline, which roughly doubles ingestion code complexity. If no (all drawings have labels and Gemini-generated captions are sufficient), skip it.

---

## Sources

- `/home/lukasthomas/sleek-rag/.agent/research/google-rag-research.md` (April 2026 GCP RAG offerings, pricing, capabilities)
- `/home/lukasthomas/sleek-rag/.agent/research/codebase-map.md` (current OpenAI integration shape and coupling points)
- `/home/lukasthomas/sleek-rag/CLAUDE.md` (project rules: Supabase RLS, SSE streaming, Realtime ingestion status, Module 2+ OpenRouter)
- `/home/lukasthomas/sleek-rag/supabase/migrations/0001_init.sql` (current schema + RLS)
