# Sleek-RAG Codebase Map — OpenAI Integration & Architecture

**Date:** 2026-04-27  
**Purpose:** Understand current OpenAI RAG setup for migration planning to Google Cloud RAG solution.

---

## 1. Architecture & Stack Confirmation

### Stack (as implemented)
- **Frontend:** Next.js 16 + TypeScript, App Router, Tailwind + shadcn/ui
- **Backend:** Python 3.10 + FastAPI (no async streaming on file ops, uses `asyncio.to_thread`)
- **Database:** Supabase (Postgres + pgvector + Auth + Realtime)
- **LLM:** OpenAI Responses API (Module 1, managed RAG with Vector Stores)
- **Observability:** LangSmith (wraps OpenAI calls automatically)

**Deviation from CLAUDE.md:** None noted. Architecture matches specification.

### Key Files
- `/home/lukasthomas/sleek-rag/backend/app/main.py:1-35` — FastAPI app setup with CORS, routers
- `/home/lukasthomas/sleek-rag/backend/app/config.py:1-27` — Pydantic settings (OpenAI key, Supabase URL, LangSmith config)

---

## 2. OpenAI RAG Integration (The Critical Path)

### 2.1 OpenAI Feature Being Used
**Responses API + File Search (Managed RAG)**

The app uses OpenAI's **Responses API** (`responses.create()`) with **file_search** tool + **Vector Stores** for per-project document indexing:
- Per-project Vector Store is created on first file upload or project creation (lazy + background)
- Files are chunked, embedded, and indexed by OpenAI
- At query time, `file_search` tool is attached to the Responses API call
- OpenAI retrieves and ranks chunks internally; app receives only the final response text

**Where it's called:**
- `/home/lukasthomas/sleek-rag/backend/app/routers/chats.py:226` — Main chat completion: `openai_client().responses.create()`
- `/home/lukasthomas/sleek-rag/backend/app/routers/chats.py:299` — Title generation: `openai_client().responses.create()`

### 2.2 Document Upload Flow

**File Upload Endpoint:** `/api/projects/{project_id}/files` (POST)  
**File:** `/home/lukasthomas/sleek-rag/backend/app/routers/files.py:71-119`

**Step-by-step:**
1. Frontend POSTs `FormData` with file to `/api/projects/{project_id}/files` (line 481 in app-shell.tsx)
2. Backend reads file bytes (line 80: `await file.read()`)
3. Calls `_ensure_vector_store(project)` (line 84) — creates per-project OpenAI vector store if missing
4. Calls `files_create(filename, contents)` (line 89) — uploads bytes to OpenAI `/files` endpoint
5. Calls `vs_ingest_file(vector_store_id, file_id)` (line 94) — ingests file into vector store, polls until completion
6. Stores metadata in Supabase `project_files` table: `id`, `filename`, `size_bytes`, `openai_file_id`, `status`
7. Returns `FileOut` response with final status (`indexed` or error)

**Database Persistence:**
- `openai_file_id` stored in `project_files.openai_file_id` (text)
- `openai_vector_store_id` stored in `projects.openai_vector_store_id` (text)
- Status (`indexed`, `pending`, `failed`) stored in `project_files.status`

**No polling for ingestion status after upload:** The `vs_ingest_file()` call (line 56 in openai_client.py) polls synchronously with `create_and_poll()` and returns final status. Frontend is NOT notified of intermediate progress via Realtime — status is reported once in the upload response.

**Files:** `/home/lukasthomas/sleek-rag/backend/app/routers/files.py:42-52` (lazy vector store creation)

### 2.3 Chat Query Flow

**Chat Send Endpoint:** `/api/chats/{chat_id}/messages` (POST)  
**File:** `/home/lukasthomas/sleek-rag/backend/app/routers/chats.py:166-287`

**Step-by-step:**
1. Frontend sends JSON: `{ text: "...", projektanalyse_template: [...] }` (app-shell.tsx:864)
2. Backend loads chat from Supabase, retrieves its `openai_thread_id` (line 131)
3. If thread doesn't exist, creates new conversation: `conversation_create()` (line 182)
4. Looks up project and gets `openai_vector_store_id` (line 192-200)
5. Builds `tools` array: always includes `PROJEKTANALYSE_TOOL` + `PROJEKTANALYSE_V2_TOOL`
   - If `vector_store_id` exists, appends `file_search` tool (line 205-210)
6. Calls `openai_client().responses.create()` with:
   - `model="gpt-4o-mini"`
   - `conversation=openai_thread_id`
   - `stream=True` for token-by-token SSE
   - `tools=[...]` including file_search
   - `instructions=PROJEKTANALYSE_INSTRUCTIONS`
   - `include=["file_search_call.results"]` if vector_store present (surfaces chunk content + scores in trace)
7. Streams response events to frontend via SSE

**Conversation Management:**
- Stateless from app's perspective: all history lives in OpenAI conversation
- Frontend loads history on chat open via `/api/chats/{chat_id}/messages` (GET, line 127)
- Backend calls `openai_client().conversations.items.list()` (line 133) to fetch all items from the thread
- Returns chronologically-ordered messages

**Files:**
- `/home/lukasthomas/sleek-rag/backend/app/routers/chats.py:127-151` — Load message history
- `/home/lukasthomas/sleek-rag/backend/app/openai_client.py:80-82` — `conversation_create()`

### 2.4 SSE Streaming Implementation

**Backend (Python):**
- `/home/lukasthomas/sleek-rag/backend/app/routers/chats.py:226` — Calls `openai_client().responses.create()` with `stream=True`
- Iterates over `stream` events, filters `response.output_text.delta` (line 231)
- Yields SSE frame: `f"data: {json.dumps({'delta': event.delta})}\n\n"` (line 232)
- Handles tool calls (projektanalyse trigger) by breaking stream early (line 234-237)
- Returns `StreamingResponse` with `media_type="text/event-stream"` (line 278-287)

**Frontend (TypeScript):**
- `/home/lukasthomas/sleek-rag/components/app-shell.tsx:874-919`
- Uses `ReadableStream` + `TextDecoder` to consume SSE
- Parses lines starting with `data: `, extracts JSON `{ delta?: string }`
- Accumulates deltas into assistant message content in real-time (line 910)
- Supports abortion via `AbortController` (line 837-838)

**Files:**
- Backend: `/home/lukasthomas/sleek-rag/backend/app/routers/chats.py:226-238`
- Frontend: `/home/lukasthomas/sleek-rag/components/app-shell.tsx:874-919`

### 2.5 Citations & Response Structure

**Current Implementation: None**

OpenAI Responses API + file_search does **not expose citations** in the response payload. The app:
- Receives only the final text response
- Does not extract file references, chunk IDs, or page numbers
- Does not render any citation UI

If `include=["file_search_call.results"]` is set (line 224 in chats.py), the results appear in **LangSmith traces only**, not in the actual response text returned to the user.

**Frontend Citation UI:** Absent. No components render citations or source references.

---

## 3. Document Ingestion Flow

### Upload UI
**File:** `/home/lukasthomas/sleek-rag/components/project-files-modal.tsx`

- Modal triggered from sidebar or chat composer
- Supports drag-drop and file picker
- Filters files via `filterAllowedFiles()` (accepts: PDF, DOCX, CSV, image, TXT, MD)
- Shows progress: file appears with status "analyzing" → "complete" when backend responds

**No streaming status updates via Realtime:** Frontend polls by GETting `/api/projects/{project_id}/files` on modal open (line 388 in app-shell.tsx), does not subscribe to Realtime.

### Backend Ingestion
- **Synchronous polling:** `vs_ingest_file()` blocks until OpenAI finishes (openai_client.py:56)
- **No async job queue:** All work happens in the HTTP request lifecycle
- **Status stored in Supabase:** `project_files.status` enum (`pending`, `indexed`, `failed`)

**File:** `/home/lukasthomas/sleek-rag/backend/app/routers/files.py:71-119`

### Metadata Storage
- **In Supabase:**
  - `projects.openai_vector_store_id` (UUID string)
  - `project_files.openai_file_id` (file_id from OpenAI)
  - `project_files.status` (indexed/failed)
- **In OpenAI:** Files and vector store (not synced back to app; app holds only IDs)

---

## 4. Chat Flow

### Frontend Chat UI
**File:** `/home/lukasthomas/sleek-rag/components/chat.tsx`

- **Message component:** renders user/assistant text, no citations
- **Composer component:** textarea, model selector (for UI only—backend always uses gpt-4o-mini), temperature slider, file attach (UI only)
- **Empty state:** suggestions to trigger Projektanalyse v1/v2
- **No message annotations:** message content is plain text after streaming completes

**File:** `/home/lukasthomas/sleek-rag/components/app-shell.tsx:797-982` (main send loop)

### Backend Chat Endpoint
**Stateless with client-side history:**
- CLAUDE.md says "Module 2+ uses stateless completions — store and send chat history yourself" but Module 1 uses OpenAI Conversations API
- Actually: **History lives in OpenAI thread**, not sent per-request; backend reads from conversation on demand
- No client-side history replay

### SSE Streaming
- Token-by-token deltas streamed as SSE
- Frontend accumulates into message.content
- No citation metadata in stream

---

## 5. Auth & Multi-Tenancy

### Supabase Auth Integration
**File:** `/home/lukasthomas/sleek-rag/backend/app/auth.py`

- FastAPI JWT validation via `current_user_id()` (line 13)
- Fetches signing key from Supabase JWKS endpoint (line 10)
- Validates JWT audience, extracts `sub` (user UUID)

**Frontend:** Supabase `supabase-js` client in `/lib/supabase/client.ts` (not shown in snippets but used in app-shell.tsx line 22)

### Row-Level Security
All tables have RLS enabled:
- `projects`: `auth.uid() = user_id`
- `chats`: `auth.uid() = user_id`
- `project_files`: `auth.uid() = user_id`

**File:** `/home/lukasthomas/sleek-rag/supabase/migrations/0001_init.sql:34-43`

### Project-Level Scoping
- Documents belong to a project, project belongs to a user
- Vector store is per-project (one `openai_vector_store_id` per project)
- Chats belong to a project
- No cross-user or cross-project leakage due to RLS

**Signup Trigger:** Auto-creates "My Project" for new users (line 46-56 in init.sql)

---

## 6. Database Schema

### Tables
**File:** `/home/lukasthomas/sleek-rag/supabase/migrations/0001_init.sql`

```sql
projects (
  id uuid,
  user_id uuid (FK auth.users),
  name text,
  openai_vector_store_id text,  -- KEY: stores OpenAI vector store ID
  created_at timestamptz
)

chats (
  id uuid,
  project_id uuid (FK projects),
  user_id uuid (FK auth.users),
  title text,
  openai_thread_id text,  -- KEY: stores OpenAI conversation ID
  created_at timestamptz
)

project_files (
  id uuid,
  project_id uuid (FK projects),
  user_id uuid (FK auth.users),
  filename text,
  size_bytes bigint,
  openai_file_id text,  -- KEY: stores OpenAI file ID
  status text (enum-like: pending, indexed, failed),
  created_at timestamptz
)
```

### Key Columns for RAG
- `projects.openai_vector_store_id` — per-project document index
- `chats.openai_thread_id` — conversation context
- `project_files.openai_file_id` — file reference in OpenAI
- `project_files.status` — ingestion result

No columns for:
- Page numbers, chunk IDs, embedding vectors (all managed by OpenAI)
- Citations or references
- File content or metadata beyond name/size

---

## 7. Frontend Components for Citations & Images

### Current State
**No citation or image rendering components exist.**

- Message rendering: plain Markdown via `react-markdown` (chat.tsx:46)
- No special handling for cited sources or file references
- No image display components for technical drawings
- File modal shows file list with name/size/status, not file content

### What Would Change
To support page-number citations and inline images:
1. **Response structure:** Need to extract citations from LLM response or embed them in message
2. **Citation UI component:** Render `[source.pdf, p. 42]` links, highlight in text
3. **Image display:** Embed `<img>` tags in message Markdown or render separately
4. **File preview:** Store thumbnail or original file reference for drawing display
5. **Message metadata:** Augment message type to include `{ role, content, citations?: Citation[] }`

---

## 8. Coupling Points — OpenAI Tight Integration

### Tightly Coupled (Hard to Swap)
1. **Responses API + Vector Stores:** Entire chat flow is built on `openai_client().responses.create()`
   - Line 226 in chats.py: Direct call, no abstraction
   - No interface; swapping would require rewriting the entire flow
   
2. **File Upload:** Direct `files.create()` + `vs_ingest_file()`
   - Lines 89, 94 in files.py
   - Tightly tied to OpenAI's Vector Store API
   - No abstraction for pluggable retrieval
   
3. **Conversation Management:** `openai_client().conversations.*`
   - Lines 133, 182 in chats.py
   - Tied to OpenAI Conversations API for context storage
   - Would need to move history management to Supabase for a swap

4. **LangSmith Instrumentation:** Wraps OpenAI client globally
   - Line 15 in openai_client.py: `wrap_openai(raw)`
   - Assumes OpenAI SDK structure; would need custom tracing for new provider

### Moderately Coupled
1. **SSE Format:** Backend yields `{ delta: string }` JSON
   - Could be adapted to include citations if new provider returns them
   - Frontend would need minimal changes

2. **Tool Definitions:** `PROJEKTANALYSE_TOOL`, `PROJEKTANALYSE_V2_TOOL`
   - Custom tools for domain-specific analysis
   - Could be reused with any provider supporting function calling

### Loosely Coupled
1. **Supabase Storage:** Tables store only IDs/metadata
   - Could store GCP resource IDs instead of OpenAI ones
   - RLS policies remain unchanged

2. **Frontend API Client:** Generic `api()` fetch wrapper
   - Backend endpoint contracts are provider-agnostic
   - No OpenAI-specific logic on client

---

## Summary: Migration Path Considerations

### What Must Change
- **Responses API** → **GCP Generative AI** chat/completion calls
- **Vector Stores** → **Vertex AI Search** or **Semantic Retriever** with page-number extraction
- **File ingestion** → **GCP document processing** pipeline (async, with extraction of page numbers)
- **Conversation storage** → Move from OpenAI Conversations API to Supabase (store history client-side or server-side)
- **Citation handling** → Extract page numbers from GCP retrieval and render in frontend

### What Can Stay
- Supabase schema (add columns for GCP resource IDs if needed)
- RLS policies and auth flow
- Frontend SSE streaming (if GCP API supports streaming)
- Projektanalyse tool definitions (adapt to new provider)
- LangSmith integration (custom tracing wrapper)

### High-Risk Areas
1. **Page-number extraction:** GCP's file_search may not expose granular page refs in the same way
2. **Async ingestion:** GCP's document processing is async; current sync polling needs refactoring
3. **Image retrieval:** Must confirm GCP can extract and reference images from PDFs
4. **Conversation history:** Needs decision: client-side replay or Supabase-backed sessions?

---

## File Reference Quick Index

**Backend Core:**
- `/home/lukasthomas/sleek-rag/backend/app/main.py` — FastAPI setup
- `/home/lukasthomas/sleek-rag/backend/app/openai_client.py` — OpenAI wrapper + LangSmith
- `/home/lukasthomas/sleek-rag/backend/app/routers/chats.py` — Chat + responses
- `/home/lukasthomas/sleek-rag/backend/app/routers/files.py` — File upload + vector store
- `/home/lukasthomas/sleek-rag/backend/app/routers/projects.py` — Project CRUD + vector store provisioning

**Frontend Core:**
- `/home/lukasthomas/sleek-rag/components/app-shell.tsx` — Main shell, SSE message loop (line 874)
- `/home/lukasthomas/sleek-rag/components/chat.tsx` — Message & composer components
- `/home/lukasthomas/sleek-rag/components/project-files-modal.tsx` — File upload UI

**Database:**
- `/home/lukasthomas/sleek-rag/supabase/migrations/0001_init.sql` — Schema + RLS

---

