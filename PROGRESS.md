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
