# Progress

Track your progress through the masterclass. Update this file as you complete modules - Claude Code reads this to understand where you are in the project.

## Convention
- `[ ]` = Not started
- `[-]` = In progress
- `[x]` = Completed

## Modules

### Module 1: App Shell + Observability

- [x] Frontend design ported from claude.ai/design bundle (Next.js 16 + TS, App Router). All UI mocked client-side. See `.agent/plans/1.design-port.md`.
- [-] Auth (Supabase) — code complete (`.agent/plans/5.supabase-auth.md`); pending: apply `supabase/migrations/0001_init.sql` in Supabase dashboard, end-to-end signup test.
- [-] OpenAI Responses API integration — code complete (`.agent/plans/6.responses-api-chat.md`, `.agent/plans/7.file-search-vector-stores.md`); SSE streaming, conversations API, file_search wired against shared `VECTOR_STORE_ID`. Pending: live smoke test.
- [-] LangSmith tracing — code complete (`.agent/plans/8.langsmith-tracing.md`); `wrap_openai` auto-traces all OpenAI calls when `LANGSMITH_API_KEY` is set. Pending: trace verified in LangSmith UI.

### Module 1 — outstanding manual steps before validation

1. Apply `supabase/migrations/0001_init.sql` in the Supabase Dashboard SQL editor (creates `projects`, `chats`, `project_files` with RLS + signup trigger that seeds a default project).
2. Confirm `.env` is fully populated (Supabase URL/keys/JWT secret, OpenAI key, `VECTOR_STORE_ID`, LangSmith key).
3. Start backend: `cd backend && source venv/bin/activate && uvicorn app.main:app --reload --port 8000`.
4. Start frontend: `npm run dev`.
5. End-to-end smoke: sign up → see seeded project → send a chat → see SSE tokens stream → upload a PDF via ProjectFilesModal → ask a follow-up referencing the doc → check trace in LangSmith UI.
