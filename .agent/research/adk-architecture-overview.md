# sleek-rag chat architecture — post-19.0 ADK multi-agent (high level)

Companion overview to [`../plans/19.0.adk-multi-agent-chat.md`](../plans/19.0.adk-multi-agent-chat.md). Visual reference for the component topology, per-turn data flow, lifecycle scopes, invariants, and the future migration path to Vertex Agent Engine Sessions.

---

## Component topology

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Browser (NextJS / chat.tsx + app-shell.tsx)                                │
│  • SSE consumer: {type: delta} → append, {type: meta} → render chips, done  │
│  • linkifyCitations regex on rendered markdown — order-independent          │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │  POST /api/chats/{id}/messages
                                     ▼  (text/event-stream)
┌─────────────────────────────────────────────────────────────────────────────┐
│  FastAPI backend — backend/app/routers/chats.py                             │
│  ─────────────────────────────────────────────────────────────────────────  │
│  _send_message_stream(chat, text, ...)                                      │
│    1. persist user msg → Supabase chat_messages                             │
│    2. resolve corpus_name from projects table                               │
│    3. get_or_build_app(corpus_name) ────────────┐                           │
│    4. seed_session(history from chat_messages) ─┤                           │
│    5. async for event in app.async_stream_query(message=text, session_id):  │
│         • orchestrator deltas → SSE delta frames                            │
│         • v2 handoff sentinel  → break, resume from stream_projektanalyse_v2│
│    6. read state["citations"] → dedupe + renumber → SSE meta                │
│    7. persist assistant msg + citations → chat_messages                     │
└──────────────────────────────────────────────────┬──────────────────────────┘
                                                   │
                                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Per-corpus AdkApp cache  (backend/app/adk/app_factory.py)                  │
│  ─────────────────────────────────────────────────────────────────────────  │
│  OrderedDict[corpus_name → AdkApp]   max=256, LRU, async-locked             │
│  All apps share app_name="sleek-rag" + InMemorySessionService               │
│                                                                             │
│   ┌────────────────────────────┐   ┌────────────────────────────┐           │
│   │  AdkApp(corpus=projA)      │   │  AdkApp(corpus=projB)      │   …       │
│   │   = make_chat_orchestrator │   │                            │           │
│   └─────────────┬──────────────┘   └────────────────────────────┘           │
└─────────────────┼───────────────────────────────────────────────────────────┘
                  │
                  ▼  agent tree (one per corpus)
┌─────────────────────────────────────────────────────────────────────────────┐
│  chat_orchestrator   gemini-2.5-pro                                         │
│  ─────────────────────────────────────────────────────────────────────────  │
│  • Routing: smalltalk / pure-followup / contextual / new / web / multi /    │
│    compound                                                                 │
│  • Pronoun resolution + multi-question split + compound-question synthesis  │
│  • Citation renumbering directive                                           │
│  • Tools (AgentTools, NOT sub_agents):                                      │
│                                                                             │
│   ┌──────────────────────────┐      ┌────────────────────────────┐          │
│   │ rag_specialist (Flash)   │      │ web_researcher (Flash)     │          │
│   │ • SIA domain rules       │      │ • German Schweiz, no umlaut│          │
│   │ • [N]-placement contract │      │ • URL citations            │          │
│   │ • SEITEN-NULL clause     │      │                            │          │
│   │ • Tool ▼                 │      │ • Tools ▼                  │          │
│   └────────────┬─────────────┘      └─────┬──────────────────────┘          │
│                │                          │                                 │
│                ▼                          ▼                                 │
│   ┌──────────────────────────┐    ┌──────────────────────────┐              │
│   │ document_retriever       │    │ web_google_search        │              │
│   │ (thin wrapper)           │    │  (GoogleSearchTool)      │              │
│   │ Tool ▼                   │    │                          │              │
│   └────────────┬─────────────┘    │ web_url_fetcher          │              │
│                │                  │  (url_context)           │              │
│                ▼                  └──────────────────────────┘              │
│   ┌──────────────────────────────────────────────────┐                      │
│   │ CitationPreservingRagRetrieval                   │                      │
│   │  (subclass of VertexAiRagRetrieval)              │                      │
│   │  • Override run_async →                          │                      │
│   │     rag.async_retrieve_contexts(rag_resources=[  │                      │
│   │       RagResource(rag_corpus=corpus_name)])      │                      │
│   │  • Regex-extract [Seite N] / [Abb. N: …]         │                      │
│   │  • Write structured records to                   │                      │
│   │     tool_context.state["citations"]              │                      │
│   │  • Return {chunks: [{idx, filename, page, …}]}   │                      │
│   └──────────────────┬───────────────────────────────┘                      │
│                                                                             │
│   ┌──────────────────────────────────────────────────┐                      │
│   │ run_projektanalyse_v2 (FunctionTool)             │                      │
│   │  • Returns sentinel {hand_off: "projektanalyse_v2"}│                    │
│   │  • chats.py sees sentinel → exits ADK stream,     │                     │
│   │    drives the existing v2 streamer               │                      │
│   └──────────────────────────────────────────────────┘                      │
└────────────────────┼────────────────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────┐    ┌───────────────────────────────────┐
│  Vertex AI RAG Engine           │    │  Supabase (source of truth)       │
│  • One ragCorpora/{id} per      │    │  • projects.rag_corpus_name       │
│    project                      │    │  • chat_messages (role, content,  │
│  • Hybrid retrieval, top_k=10   │    │    citations) — replayed per turn │
│  • Chunks carry [Seite N] /     │    │  • project_files                  │
│    [Abb. N: …] from LLM Parser  │    │  • Realtime ingestion status      │
└─────────────────────────────────┘    └───────────────────────────────────┘
```

---

## Per-turn data flow

```
USER: "Und welche Firma vertritt er?"  (after history mentions Hans Mueller)
  │
  ▼
┌─ chats.py ──────────────────────────────────────────────────────────────┐
│ 1. INSERT into chat_messages (user turn)                                │
│ 2. SELECT rag_corpus_name FROM projects WHERE id=…                      │
│ 3. app = get_or_build_app(corpus)              [LRU hit or build]       │
│ 4. session = InMemorySessionService.create_session(app_name="sleek-rag")│
│    └ replay 20 prior chat_messages → session.append_event(…)            │
│ 5. async for event in app.async_stream_query(message=text, session.id): │
└─────────────────────────────────────────┬───────────────────────────────┘
                                          │
                                          ▼
┌─ chat_orchestrator (Pro) ───────────────────────────────────────────────┐
│ • Reads history → "er" = "Hans Mueller"                                 │
│ • Decides: KONTEXT-ABHAENGIGE FOLGEFRAGE, dispatch with rephrased query │
│ • Calls rag_specialist(question="Welche Firma vertritt Hans Mueller …") │
└─────────────────────────────────────────┬───────────────────────────────┘
                                          ▼
┌─ rag_specialist (Flash) ────────────────────────────────────────────────┐
│ • Calls document_retriever                                              │
└─────────────────────────────────────────┬───────────────────────────────┘
                                          ▼
┌─ document_retriever (Flash) ────────────────────────────────────────────┐
│ • Calls CitationPreservingRagRetrieval(query="…Hans Mueller…")          │
└─────────────────────────────────────────┬───────────────────────────────┘
                                          ▼
┌─ CitationPreservingRagRetrieval.run_async ──────────────────────────────┐
│ • rag.async_retrieve_contexts(text=…, rag_resources=[corpus])           │
│ • regex [Seite N] → page_start                                          │
│ • tool_context.state["citations"] += [{idx:1, file:"…", page:21, …}, …] │
│ • return {status:"ok", chunks:[…]}                                      │
└─────────────────────────────────────────┬───────────────────────────────┘
                                          ▼
┌─ rag_specialist composes answer ────────────────────────────────────────┐
│ "Thomas Kieliger ist bei der Acme AG[1] taetig."                        │
└─────────────────────────────────────────┬───────────────────────────────┘
                                          ▼
┌─ chat_orchestrator finalizes ───────────────────────────────────────────┐
│ • Forwards rag_specialist answer (deltas streamed live to chats.py)     │
│ • If multi-question: aggregates N answers, instructs renumbering        │
└─────────────────────────────────────────┬───────────────────────────────┘
                                          │
                                          ▼  back in chats.py
┌─ post-stream aggregation ───────────────────────────────────────────────┐
│ • final_session = session_service.get_session(…)                        │
│ • dedupe_and_renumber(state["citations"]) → final list + remap          │
│ • rewrite_refs(answer, remap) — fix [N] markers if dedup changed numbers│
│ • SSE meta {citations, content: annotated}                              │
│ • INSERT into chat_messages (assistant turn + citations JSON)           │
│ • SSE done                                                              │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Lifecycle by scope

| Scope | What lives there | When it dies |
|---|---|---|
| **Process** | `_apps` LRU dict (256 max), per-corpus `AdkApp` instances, all sub-agent definitions | Process exit; LRU eviction beyond 256 (in-flight refs survive eviction) |
| **Per request (one user turn)** | `InMemorySessionService` session; seeded events from Supabase replay; `state["citations"]` accumulator | After SSE `done` frame is emitted; GC'd when handler returns |
| **Per chat (durable)** | `chat_messages` rows in Supabase (role, content, citations JSON), `chats` row, `projects.rag_corpus_name` | User deletes chat |
| **Per project (durable)** | `ragCorpora/{id}` in Vertex (chunks + embeddings), `project_files` rows, GCS originals | User deletes project |

---

## Key invariants the architecture enforces

1. **Citations always come from a tool result, never from answer prose.** The subclass writes structured records; the orchestrator never invents them.
2. **The orchestrator is the only agent the user "talks to".** Sub-agent text is filtered out of the SSE stream by `event_author == "chat_orchestrator"`.
3. **Each turn is stateless from ADK's perspective.** Session created, replayed, run, discarded. The only durable chat memory is in Supabase.
4. **Corpus binding is structural, not contextual.** `corpus_name` is closed over by the `CitationPreservingRagRetrieval` instance at factory-build time. No way for a session to retrieve from the wrong project's docs.
5. **Domain rules live where they're enforced.** Routing rules → orchestrator instruction. Retrieval/answer rules → rag_specialist instruction. NO-V2-ESCALATION → orchestrator (it owns the v2 tool).

---

## Where the migration to Vertex sessions later (option `a`) plugs in

A single line change in `app_factory.py`:

```python
# Before (19.0)
session_service_builder=lambda: InMemorySessionService()

# After (future phase)
session_service_builder=lambda: VertexAiSessionService(
    project=settings.gcp_project_id,
    location=settings.gcp_location,
    agent_engine_id=settings.adk_agent_engine_id,  # one empty Agent Engine per env
)
```

`chats.py` stops replaying from Supabase per turn, persists session_id alongside `chats` rows, and the chat-list UI starts reading from `app.async_list_sessions()` (or keeps reading Supabase if we dual-write). That's the entire migration. Nothing in the agent tree changes.
