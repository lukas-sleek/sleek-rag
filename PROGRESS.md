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
- [ ] Plan 13 — `.agent/plans/13.citation-image-ui-and-cleanup.md` — citation chips, figure thumbnails, PDF viewer, OpenAI removal. ⚠️ Medium.

**Status:** Manual GCP/Supabase setup complete. Ready to kick off plan 10 execution.
