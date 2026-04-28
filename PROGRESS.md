# Progress

Track your progress through the masterclass. Update this file as you complete modules - Claude Code reads this to understand where you are in the project.

## Convention
- `[ ]` = Not started
- `[-]` = In progress
- `[x]` = Completed

## Modules

### Module 1: App Shell + Observability

- [x] Frontend design ported from claude.ai/design bundle (Next.js 16 + TS, App Router). See `.agent/plans/1.design-port.md`.
- [x] Auth (Supabase) — `.agent/plans/5.supabase-auth.md`. Email/password via `supabase-js`, SSR cookies via `@supabase/ssr`, FastAPI JWT validation, RLS-protected `projects` / `chats` / `project_files`, signup trigger seeds a default project. Migration `supabase/migrations/0001_init.sql` applied to the "RAG" Supabase project. Test user `test@test.com` / `12345678` available — see CLAUDE.md.
- [x] OpenAI Responses API integration — `.agent/plans/6.responses-api-chat.md` + `.agent/plans/7.file-search-vector-stores.md`. SSE streaming via `responses.stream`, persistent conversations via `conversations.create()`/`items.list()`, per-project Vector Stores auto-created on first file upload, `file_search` tool attached when the project has files. **Managed RAG** — OpenAI handles chunking, embedding, retrieval, and ranking; we just upload bytes.
- [x] LangSmith tracing — `.agent/plans/8.langsmith-tracing.md`. `wrap_openai` auto-traces every OpenAI call (responses, conversations, files, vector_stores) when `LANGSMITH_API_KEY` is set. No instrumentation required at call sites.

**Status:** Module 1 code complete and ready for end-to-end smoke testing.

### Module 1 — End-to-End Smoke Test

1. Backend: `cd backend && source venv/bin/activate && uvicorn app.main:app --reload --port 8000`
2. Frontend: `npm run dev`
3. Open `http://localhost:3000`, sign in with `test@test.com` / `12345678` (or sign up a fresh account).
4. Confirm the seeded "My Project" appears in the sidebar.
5. Create a chat, send a message → SSE tokens stream into the assistant bubble in real time. Refresh the page → history reloads.
6. Open ProjectFilesModal, upload a PDF → row flips to "complete" once OpenAI finishes indexing the per-project vector store.
7. Ask a follow-up that references the doc → response cites file content (Responses API + `file_search`).
8. Open https://smith.langchain.com/ → project `sleek-rag` → see the chat completion + file ingestion runs traced.

### Next: Module 2 — BYO Retrieval + Memory

The PRD's M1 → M2 architectural decision (PRD.md §"Module 1 → Module 2 Transition") commits to **Option A: Replace** — strip the Responses API + Vector Store managed-RAG path entirely and rebuild on the standard Chat Completions API with own ingestion (chunking → embeddings → pgvector → retrieval tool) plus client-side conversation history. M1's managed-RAG code is intentionally throwaway.

---

### Module 1.5 — Google Cloud RAG Migration (in progress)

Driver: M1's OpenAI managed-RAG cannot return page-number citations or retrieve images of technical drawings. Rather than continuing to M2 with OpenAI, we replace it with a Google Cloud stack — Document AI Layout Parser (page spans + figure bytes natively) + Gemini 2.5 Flash via the OpenAI-compatible endpoint + self-hosted retrieval in Supabase pgvector.

Branch: `feat/google-rag-migration`. Decision basis: `.agent/research/recommendation.md` (synthesized from `google-rag-research.md` + `codebase-map.md`).

#### Manual GCP setup — completed 2026-04-28

- [x] GCP project `sleek-rag` (number `1007445049099`) created, billing linked
- [x] APIs enabled: Document AI, Generative Language, Cloud Storage, IAM Credentials
- [x] Document AI Layout Parser processor `158602e037219e17` provisioned in `eu` multi-region
- [x] Service account `sleek-rag-backend@sleek-rag.iam.gserviceaccount.com` created with `roles/documentai.apiUser` + `roles/storage.objectAdmin`; key file at `~/.config/sleek-rag/sleek-rag-b80deaaff5c7.json` (chmod 600, outside repo)
- [x] Gemini API key provisioned via AI Studio, stored in `.env` as `GEMINI_API_KEY`. Smoke-tested with `GET /v1beta/openai/models` — returns Gemini model list
- [x] GCS staging bucket `sleek-rag-staging` (`eu`) created
- [x] Supabase Storage buckets `project-files` and `chunk-images` created with RLS policies (users read own folder)
- [x] All env vars populated in `.env` (GCP_PROJECT_ID, DOCUMENTAI_*, GEMINI_*, GCS_STAGING_BUCKET)

#### Phase plans

- [x] Plan 10 — completed 2026-04-28. Schema migration `0002_google_rag.sql` applied to RAG project; Gemini + Document AI clients live; smoke tests pass.
  - Deviation: gemini smoke test passes `extra_body={"reasoning_effort": "none"}` because gemini-2.5-flash spends thinking tokens that exceed the plan's `max_tokens=10` cap, returning `content=None` otherwise.
  - Deviation: gemini embeddings smoke test passes `dimensions=settings.gemini_embedding_dim` because `gemini-embedding-001` defaults to 3072 dims; we pin to 768 to match the pgvector column.
  - Deviation: initial Document AI processor (`158602e037219e17`) was provisioned as `CUSTOM_EXTRACTION_PROCESSOR`; replaced with a `LAYOUT_PARSER_PROCESSOR` (`d7fc4648a95684c0`) and `.env` updated. The service account also needed `roles/documentai.viewer` (in addition to `roles/documentai.apiUser`) for the validation `list_processors` call.
- [x] Plan 11 — completed 2026-04-28. Async ingestion live; e2e test ingested a 4-page PDF (`somatosensory.pdf`) in 16.9s producing 9 chunks with 768-dim embeddings, and a separate 8-page engineering PDF in 30.6s producing 26 chunks + 17 figure images persisted to `chunk-images` Supabase Storage.
  - Deviation: Layout Parser image extraction requires `documentai_v1beta3` (not v1) plus `enable_image_extraction=True` on `LayoutConfig`. Image bytes live on `Document.blob_assets[]` keyed by `asset_id`; chunks reference them via `chunk.chunk_fields[].image_chunk_field.blob_asset_id`. The plan's `chunk.images[0].image` shape does not exist in the public SDK.
  - Deviation: `chunk.page_headers` returns `ChunkPageHeader` proto objects (not strings); the worker extracts `.text` before serializing to `heading_path[]`.
  - Deviation: migrations numbered `0006_ingest_jobs.sql` and `0007_claim_job.sql` (the next free numbers; the plan said 0003/0004 but those slots were already taken).
- [x] Plan 12 — completed 2026-04-28. Chat now answers via Gemini 2.5 Flash with hybrid retrieval (page / figure-label / heading / vector + visual-block filter) over `document_chunks`. SSE format extended to type-discriminated `meta` (citations) / `delta` / `done` (with `message_id`) frames. Chat history moved to `chat_messages`; `list_messages` reads from Supabase. Projektanalyse v1 (per-question retrieval) and v2 (full-corpus context) both swapped to `gemini_client().chat.completions.create` and verified end-to-end against a real PDF. Tool-call detection via Gemini's OpenAI-compat tool streaming works — `Erstelle mir eine Projektanalyse` triggers the v1 handoff and produces a Markdown report. Retrieval RPCs (`match_chunks`, `chunks_by_heading_prefix`) live in migration `0008_retrieval_fns.sql`.
  - Backfill: legacy chats whose history lived in OpenAI Conversations now show empty in the UI — acceptable for the test-user-only state; no migration script written.
  - Note: a benign `TypeError` is logged from `langsmith.wrappers._openai._reduce_choices` when we close the chat stream early after detecting a tool call (LangSmith wrapper trying to reduce a partial tool-call delta with no index). User-visible behavior is unaffected; revisit if the noise becomes an issue.
- [x] Plan 13 — completed 2026-04-28. Citation chips + figure thumbnails + PDF viewer dialog wired into the assistant message renderer; inline `[N]` markers are clickable. PDF preview button added to the project files modal. OpenAI integration removed: `openai_client.py` deleted, `openai_*` columns dropped via `0009_drop_openai.sql`, `OPENAI_API_KEY` removed from config + env template. The `openai` Python package stays in requirements.txt as the SDK transport for Gemini's OpenAI-compatible endpoint (used only by `gemini_client.py`). End-to-end verified by uploading the converted Word document `HO_Teil_C1_Südi-Areal-Infrastruktur_def_Word.docx` (11 pages → 12 chunks).
  - Out-of-plan fixes shipped alongside the UI work:
    - Storage key sanitization in `routers/files.py` — keys are now `{user_id}/{file_id}/source.{ext}` so non-ASCII filenames (umlauts, spaces) no longer trigger Supabase Storage `InvalidKey` 400s.
    - Office→PDF auto-conversion via headless LibreOffice — `.doc/.docx/.xls/.xlsx/.ppt/.pptx/.odt/.ods/.odp/.rtf` etc. are converted to PDF on upload before being sent to Document AI's PDF-only Layout Parser. Original filename is preserved in the DB for display.
  - Known LibreOffice caveat: missing fonts on the backend host (Calibri/Aptos/Segoe UI) inflate page count after conversion (a 1-page Word doc rendered to 11 PDF pages in testing). Fix is OS-level (install MS core fonts), not pipeline-level.
  - Frontend Realtime payload (`lib/supabase/realtime.ts`) only models `chunk_count` — `page_count` updates don't propagate via the channel. Refreshing the modal calls the GET endpoint and shows the correct value. Minor; left for follow-up.

**Status:** Module 1.5 (Google Cloud RAG migration) complete. Single LLM provider: Gemini 2.5 Flash via OpenAI-compatible endpoint. Citation chips, figure thumbnails, and PDF viewer all working. OpenAI vendor integration fully removed.

- [x] Plan 14 — completed 2026-04-28. Replaced the regex retrieval router with an agentic tool loop. The chat path now injects a per-project file inventory into the system prompt (8-char file_id prefixes + page counts), exposes a single `search_chunks` tool with structured filters (`query`, `file_ids`, `page`, `figure_label`, `section`, `block_type`, `top_k`), and runs the Gemini chat as a 3-iteration loop that intercepts tool calls, executes them, and feeds results back. Citations meta frame is now emitted *after* the answer text and contains only chunks the model actually retrieved (deduped by `chunk_id`). New unified RPC `match_chunks_filtered` (migration `0010_search_chunks_filtered.sql`) handles every filter combination and returns real cosine similarity for every result — no more hardcoded 1.0 placeholders. Page-ranking rule enforced server-side: exact-page chunks rank ahead of narrow-span ahead of wide-span. Frontend got 3-bouncing-dot loading indicator (visible during the tool-call iteration before deltas start) and figures are now collapsed under `<details>Bilder anzeigen</details>`. Smoke-tested against the test user's "test" project (5 ready files): inventory renders correctly, pure-vector returns ranked chunks, page=5 returns 4 exact-page hits ahead of 1 narrow-span, file_id prefix scoping returns only the targeted file's chunks. Old regex constants (`_PAGE_RE`, `_FIGURE_RE`, `_SECTION_RE`, `_VISUAL_RE`) and `retrieve()` deleted from `app/retrieval.py`. Projektanalyse v1 temporarily uses `_by_vector` directly per question; plan 16 will migrate it to the same `search_chunks` tool.
- [-] Plan 16 — code complete 2026-04-28; live UAT pending. Hybrid retrieval (vector + Postgres FTS, fused via Reciprocal Rank Fusion) plus a Vertex AI Ranking API rerank stage stacked onto the existing `search_chunks` tool. New migration `0013_chunks_fts.sql` adds an IMMUTABLE `chunks_tsv()` wrapper + a `content_tsv` generated `tsvector` column on `document_chunks` (German config, `setweight('A')` on heading_path, `'B'` on content) and a GIN index. Migration `0014_match_chunks_hybrid.sql` provisions the hybrid RPC: pulls top-N from cosine + top-N from FTS, fuses with RRF (k=60), preserves the page-bucket tiebreaker, degrades cleanly to vector-only when `p_query` is empty. New `app/ranking_client.py` calls `discoveryengine.googleapis.com/.../rankingConfigs/default_ranking_config:rank` with `semantic-ranker-default-004` using the existing service-account JSON (just needs `roles/discoveryengine.viewer` granted out-of-band) and is fail-open: any error/timeout returns RRF order with score=0 so the chat keeps working. `execute_search_chunks` now pulls `pre_rerank_k=30` from the RPC and reranks down to the model's `top_k`; `RETRIEVAL_MODE=vector_only` is a config escape hatch back to plan-14 behavior. `CHAT_SYSTEM_PROMPT` got a synonym/fan-out instruction, the SIA-21/31 scope-fallback rule ported from `ANSWER_INSTRUCTIONS`, and an aggregation-question hint. Projektanalyse v1 (`_answer_v1_sync`) routes through the same `execute_search_chunks` with batch-tuned settings (`top_k=15`, `pre_rerank_k_override=80`); v2 (Volltext) untouched as the manual escape valve. 11 new unit tests across `test_ranking_client.py` (200/503/timeout fail-open, blank query, empty docs) and `test_search_hybrid.py` (hybrid reorders, fail-open keeps RRF order, vector_only skips rerank, override wins) — all 46 backend unit tests green. Migrations applied to the RAG Supabase project; spot-check on the test user's "test" project (94 chunks): all rows have non-null `content_tsv`, `'Schnittstellenprojekt'` FTS query returns 1 hit, hybrid RPC surfaces the page-18 Schnittstellenprojekt chunk top via RRF.
  - Deviation: the original generated-column expression `setweight(to_tsvector('german', ...), 'B') || setweight(to_tsvector('german', array_to_string(heading_path, ' ')), 'A')` failed migration with `ERROR: 42P17: generation expression is not immutable` even though every primitive (to_tsvector(regconfig, text), setweight, ||) is immutable on its own. Wrapped the whole thing in an IMMUTABLE SQL function (`public.chunks_tsv(text, text[])`) so Postgres' generated-column validator accepts it. Migration file on disk reflects what was actually applied.
  - Out-of-code prerequisites still required for end-to-end: `gcloud services enable discoveryengine.googleapis.com --project sleek-rag` and `gcloud projects add-iam-policy-binding sleek-rag --member 'serviceAccount:sleek-rag-backend@sleek-rag.iam.gserviceaccount.com' --role 'roles/discoveryengine.viewer'`. Until granted, `ranking_client.rank` returns 403 → fail-open → chat falls back to RRF order (visible in LangSmith as a 403 warn log; user-facing behavior remains correct).
  - T6 (server-side query expansion) and T8 (per-question synonym fan-out + per-file sweep in Projektanalyse) intentionally NOT shipped — plan calls them out as conditional follow-ups, gated on UAT failures of T5/T7. Will revisit after the live UAT regression run.
  - Live UAT against the test user's "test" project (the 6 chat questions in T5 + the 11-question Projektanalyse template in T7) is the remaining manual step before flipping the box to `[x]`.

- [x] Plan 15 — completed 2026-04-28. Fixed `heading_path[]` so the `search_chunks(section=…)` filter resolves deterministically against ingestion data. New parser module `backend/app/ingest_headings.py` extracts the heading hierarchy two ways: Path A peels consecutive `#+` markdown lines that Document AI prepends (the data we were throwing away), Path B regex-scans the body for inline section markers like `1.3 FRAGEN` that Layout Parser failed to mark up as headings (run-on paragraph case). The inline regex requires ≥1 dot in the section number, uppercase-led title, and a non-digit prefix — false-positive guards against `page 1.3 mio CHF` and `Tel. 31.3 sec`. Backfill migration `0011_backfill_heading_path.sql` mirrors the Python parser as a plpgsql function with 9 inline assertions covering the same test cases, then UPDATEs every existing row in place — no Document AI re-call, no re-embed. Verified end-to-end: backfilled 89/94 chunks (5 nulls are page-1 logo/banner-only chunks with no recoverable structure); `section="1.3"` now returns Teil A's run-on `1.3 FRAGEN` chunk (regression fixed) plus both C1 variants (already worked). 11 unit tests in `tests/test_ingest_headings.py` pass. Existing `chunk.page_headers` (which was the page banner, not the heading chain) is no longer written to the column. `match_chunks_filtered` RPC unchanged — its content-regex fallback branches stay as belt-and-suspenders.
  - Deviation: migration numbered `0011` instead of plan's `0012` — last existing migration was `0010`, so `0011` is the next free slot. The plan's reference to "0011 in place" for the unified RPC was a slip; that RPC is in `0010`.
  - T4 (live UAT through chat UI) deferred — requires running the backend + frontend + manually issuing `was steht in abschnitt 1.3?` against the test user's project. The data-layer fix is verified via direct SQL spot-check; the chat-path verification is a manual step.
  - Two follow-ups landed under the same plan after the file-overlay UAT surfaced gaps:
    - Path A was terminating on the first blank line; Document AI prepends ancestors as `# H1\n\n## H2\n\n### H3` (blanks between), so only the first heading was being captured and single-digit parents like `1 AUFTRAGGEBER`, `3 BEDINGUNGEN` were dropped. Fixed in both Python (`ingest_headings.py`) and SQL (migration `0012_heading_path_skip_blank_lines.sql` re-creates the function and re-runs the backfill). Teil A now surfaces 26 distinct heading entries (was 2 distinct top-level entries before).
    - `routers/files.py` `outline` was using only `heading_path[0]` per chunk — it now flattens all entries with dedup, so the file-overlay Gliederung shows the full TOC instead of just the chunk-leading heading.
