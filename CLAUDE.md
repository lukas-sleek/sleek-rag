# CLAUDE.md

RAG app with chat (default) and document ingestion interfaces. Config via env vars, no admin UI.

## Stack
- Frontend: NextJS + Tailwind + shadcn/ui + shadcn.io
- Backend: Python + FastAPI
- Database: Supabase (Postgres, pgvector, Auth, Storage, Realtime)
- LLM: Gemini 2.5 Flash via OpenAI-compatible endpoint (provider is swappable
  by changing `GEMINI_BASE_URL` + `GEMINI_API_KEY` — any OpenAI-compatible
  endpoint works, including Ollama, vLLM, OpenRouter)
- Document parsing: Google Document AI Layout Parser (eu region)
- Office→PDF conversion: headless LibreOffice on the backend host
- Embeddings: gemini-embedding-001 (768d)
- Observability: LangSmith

## Rules
- Python backend must use a `venv` virtual environment
- No LangChain, no LangGraph - raw SDK calls only
- Use Pydantic for structured LLM outputs
- All tables need Row-Level Security - users only see their own data
- Stream chat responses via SSE
- Use Supabase Realtime for ingestion status updates
- Module 2+ uses stateless completions - store and send chat history yourself
- Ingestion is manual file upload only - no connectors or automated pipelines

## Planning
- Save all plans to `.agent/plans/` folder
- Naming convention: `{sequence}.{plan-name}.md` (e.g., `1.auth-setup.md`, `2.document-ingestion.md`)
- Plans should be detailed enough to execute without ambiguity
- Each task in the plan must include at least one validation test to verify it works
- Assess complexity and single-pass feasibility - can an agent realistically complete this in one go?
- Include a complexity indicator at the top of each plan:
  - ✅ **Simple** - Single-pass executable, low risk
  - ⚠️ **Medium** - May need iteration, some complexity
  - 🔴 **Complex** - Break into sub-plans before executing

## Development Flow
1. **Plan** - Create a detailed plan and save it to `.agent/plans/`
2. **Build** - Execute the plan to implement the feature
3. **Validate** - Test and verify the implementation works correctly. Use browser testing where applicable via an appropriate MCP
4. **Iterate** - Fix any issues found during validation

## Progress
Check PROGRESS.md for current module status. Update it as you complete tasks.

## Local Test Credentials

For local dev / smoke testing the auth flow, a test user is pre-seeded in the
"RAG" Supabase project:

- Email: `test@test.com`
- Password: `12345678`

Created with email confirmation pre-applied — works for `signInWithPassword`
without needing to confirm an email. Has a default "My Project" via the signup
trigger. Use this account when testing chat / file upload end-to-end.