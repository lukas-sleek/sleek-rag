# Agentic RAG Masterclass - PRD

## What We're Building

A RAG application with two interfaces:
1. **Chat** (default view) - Threaded conversations with retrieval-augmented responses
2. **Ingestion** - Upload files manually, track processing, manage documents

This is **not** an automated pipeline with connectors. Files are uploaded manually via drag-and-drop. Configuration is via environment variables, no admin UI.

## Target Users

Technically-minded people who want to build production RAG systems using AI coding tools (Claude Code, Cursor, etc.). They don't need to know Python or React - that's the AI's job.

**They need to understand:**
- RAG concepts deeply (chunking, embeddings, retrieval, reranking)
- Codebase structure (what sits where, how pieces connect)
- How to direct AI to build what they need
- How to direct AI to fix things when they break

## Scope

### In Scope
- ✅ Document ingestion and processing
- ✅ Vector search with pgvector
- ✅ Hybrid search (keyword + vector)
- ✅ Reranking
- ✅ Metadata extraction
- ✅ Record management (deduplication)
- ✅ Multi-format support (PDF, DOCX, HTML, Markdown)
- ✅ Text-to-SQL tool
- ✅ Web search fallback
- ✅ Sub-agents with isolated context
- ✅ Chat with threads and memory
- ✅ Streaming responses
- ✅ Auth with RLS

### Out of Scope
- ❌ Knowledge graphs / GraphRAG
- ❌ Code execution / sandboxing
- ❌ Image/audio/video processing
- ❌ Fine-tuning
- ❌ Multi-tenant admin features
- ❌ Billing/payments
- ❌ Data connectors (Google Drive, SFTP, APIs, webhooks)
- ❌ Scheduled/automated ingestion
- ❌ Admin UI (config via env vars)

## Stack

| Layer | Choice |
|-------|--------|
| Frontend | NextJs 16 + TypeScript + Tailwind + shadcn/ui + shadch.io |
| Backend | Python + FastAPI |
| Database | Supabase (Postgres + pgvector + Auth + Storage + Realtime) |
| LLM (Module 1) | OpenAI Responses API (managed threads + file_search) |
| LLM (Module 2+) | Any OpenAI-compatible endpoint (OpenRouter, Ollama, LM Studio, etc.) |
| Observability | LangSmith |

## Constraints

- No LLM frameworks - raw OpenAI SDK using the standard Chat Completions API (OpenAI-compatible), Pydantic for structured outputs
- Row-Level Security on all tables - users only see their own data
- Streaming chat via SSE
- Ingestion status via Supabase Realtime

---

## Module 1: The App Shell + Observability

**Build:** Auth, chat UI, OpenAI Responses API (manages threads + file_search), LangSmith tracing

**Learn:** What RAG is, why managed RAG exists, its limitations (OpenAI handles memory and retrieval - black box)

**Note:** The Responses API is OpenAI-specific. It provides managed threads and built-in file search, but locks you into OpenAI. Module 2 transitions to the standard Chat Completions API for provider flexibility.

---

## Architectural Decision: Module 1 → Module 2 Transition

At the end of Module 1, you have a working chat app using OpenAI's **Responses API**—a managed solution where OpenAI handles threads, memory, and file search. In Module 2, you switch to the standard **Chat Completions API** to support any OpenAI-compatible provider (OpenRouter, Ollama, LM Studio, etc.).

**The decision you need to make:** What do you do with the Responses API code? Here are two common approaches, but you're not limited to these—come up with your own if it makes sense for your use case.

| Option | Approach | Pros | Cons |
|--------|----------|------|------|
| **A: Replace** | Remove Responses API code entirely, rebuild on Chat Completions | Clean codebase, single pattern, easier to maintain | Lose the ability to use OpenAI's managed RAG |
| **B: Dual Support** | Keep Responses API alongside Chat Completions, configurable per request | Flexibility to use either approach, compare them side-by-side | More complex codebase, two patterns to understand |

There is no right answer—this is a real architectural choice you'll face in building production systems.

**In the video, I chose Option A**—completely removing the Responses API code from the codebase and any related schema from the database. This keeps things simple and focused on the OpenAI-compatible Chat Completions pattern going forward.

**This is a lesson in steering Claude Code**: you need to clearly communicate your decision and guide the AI to implement it correctly. Be explicit about what you want removed, refactored, or kept.

---

## Module 2: BYO Retrieval + Memory

**Prerequisites:** Complete the architectural decision above.

**Build:** Ingestion UI, file storage, chunking → embedding → pgvector, retrieval tool, Chat Completions API integration (OpenRouter/Ollama/LM Studio), chat history storage (stateless API - you manage memory now), realtime ingestion status

**Learn:** Chunking, embeddings, vector search, tool calling, relevance thresholds, managing conversation history, **steering AI agents through architectural refactoring**

---

## Module 3: Record Manager

**Build:** Content hashing, detect changes, only process what's new/modified

**Learn:** Why naive ingestion duplicates, incremental updates

---

## Module 4: Metadata Extraction

**Build:** LLM extracts structured metadata, filter retrieval by metadata

**Learn:** Structured extraction, schema design, metadata-enhanced retrieval

---

## Module 5: Multi-Format Support

**Build:** PDF/DOCX/HTML/Markdown via docling, cascade deletes

**Learn:** Document parsing challenges, format considerations

---

## Module 6: Hybrid Search & Reranking

**Build:** Keyword + vector search, RRF combination, reranking

**Learn:** Why vector alone isn't enough, hybrid strategies, reranking

---

## Module 7: Additional Tools

**Build:** Text-to-SQL tool (query structured data), web search fallback (when docs don't have the answer)

**Learn:** Multi-tool agents, routing between structured/unstructured data, graceful fallbacks, attribution for trust

---

## Module 8: Sub-Agents

**Build:** Detect full-document scenarios, spawn isolated sub-agent with its own tools, nested tool call display in UI, show reasoning from both main agent and sub-agents

**Learn:** Context management, agent delegation, hierarchical agent display, when to isolate

---

## Success Criteria

By the end, students should have:
- ✅ A working RAG application they built with AI assistance
- ✅ Deep understanding of RAG concepts (chunking, embedding, retrieval, reranking)
- ✅ Understanding of codebase structure - what lives where, how pieces connect
- ✅ Ability to direct AI coding tools to build new features
- ✅ Ability to direct AI coding tools to debug and fix issues
- ✅ Experience with agentic patterns (multi-tool, sub-agents)
- ✅ Observability set up from day one
