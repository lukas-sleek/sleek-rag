# ADK migration — pre-plan assumption check

Companion to the planned ADK multi-agent chat replacement. Notes which architectural assumptions are verified against the current codebase / installed deps, and which need a small spike before plan tasks commit to them.

> **2026-04-30 update — architecture pivoted.** The original sketch in this doc assumed a single shared `AdkApp` with state-based corpus injection via a custom `FunctionTool`. That has been superseded by a **per-corpus `AdkApp` factory** using a subclassed `VertexAiRagRetrieval` tool. Most of the unverified items below have been answered by the deeper research in [`adk-factory-and-history.md`](adk-factory-and-history.md), which is now the load-bearing reference. This file is kept for traceability of how the design evolved.
>
> **2026-04-30 update — T0 spike completed.** All 7 probes resolved against `google-adk==1.31.1` + `google-cloud-aiplatform[rag]>=1.108.0`. Probe scripts: `backend/scripts/spike_adk/probe_*.py`.
>
> **Findings that reshape the plan:**
>
> 1. **🔴 SUBCLASS APPROACH IS DEAD FOR GEMINI 2.x.** `VertexAiRagRetrieval.process_llm_request` registers the tool as a server-side `Tool(retrieval=...)` for any Gemini 2.x model — `run_async` is never called. The `ADK_DISABLE_GEMINI_MODEL_ID_CHECK=1` env var **forces** the server-side path (it doesn't bypass it). Plan T2 must pivot to a plain `FunctionTool` wrapping `rag.async_retrieve_contexts` directly. (Probe 2 — confirmed live with `gemini-2.5-flash`.)
> 2. **`after_tool_callback` is moot under the FunctionTool pivot** — for plain FunctionTools, callbacks always fire. Issue #2629 only matters for the (now abandoned) VertexAiRagRetrieval path. (Probe 3 skipped as informational.)
> 3. **Multi-call dispatch is PARALLEL.** Two FunctionTools called in one turn started within 0.000s and overlapped 1.005s. Plan T7's parallel multi-question fan-out works as designed. (Probe 4 — verdict PARALLEL, total turn 3.08s for two 1.0s tools.)
> 4. **Event topology mapped.** `async_stream_query` yields **dicts** (already JSON-serialised), not `Event` objects. Discriminators must be derived from contents — there is **no** explicit `kind`/`type` field:
>    - **Model text response**: `content.role == "model"` AND `content.parts[*].text` present (no `function_call`)
>    - **Tool call**: `content.role == "model"` AND `content.parts[*].function_call` present (`{id, name, args}`)
>    - **Tool response**: `content.role == "user"` AND `content.parts[*].function_response` present (`{id, name, response}`)
>    - **Author**: top-level `event["author"]` = agent name (string)
>    - **State delta**: `event["actions"]["state_delta"]` is a dict (empty when no writes)
>    - **Streaming-text deltas**: by default, each model turn arrives as **one full event** (no token-level deltas) unless a `RunConfig` with `streaming_mode` is passed. T9 should evaluate whether SSE delta-streaming requires explicit RunConfig.
>    - (Probe 1 — captured raw events for both pure-text and tool-call runs.)
> 5. **History seeding via `append_event` works.** Pattern verified live: `app.async_create_session(user_id=)` → `sess_service.get_session(app_name=, user_id=, session_id=)` → `await sess_service.append_event(session, Event(author=, content=Content(role=, parts=[Part.from_text(text=)])))`. Model receives seeded history correctly. (Probe 5 — PASS, model recalled "Steinbock" from seeded turn.)
> 6. **`app_name` does NOT make sessions cross-instance for `InMemorySessionService`.** Each `AdkApp` builds its own service; sessions live per-instance. Plan claim "all apps share `app_name='sleek-rag'` so session_ids survive eviction and rebuild" is incorrect — but **doesn't matter** under strategy (c) where each turn replays Supabase rows into a fresh session anyway. Cross-instance session sharing requires an external service (e.g. `VertexAiSessionService`, `DatabaseSessionService`). (Probe 6 — `same session_service object?: False`.)
> 7. **Public AdkApp API surface mapped.** `AdkApp.__init__` does **not** accept `app_name` — it is hard-coded to `_DEFAULT_APP_NAME = "default-app-name"`. Public methods: `async_create_session`, `async_get_session`, `async_list_sessions`, `async_delete_session`, `async_stream_query`, `async_search_memory`, `async_add_session_to_memory`, `set_up`. Session service is at `app._tmpl_attrs["session_service"]` (private but stable across the v1 API; pin `google-adk` version range). The Runner exposes its own `app_name` and `session_service` as public attrs. (Probe 7 — full method list captured.)
>
> **Plan adjustments required before T2+ commit:**
> - **T2 rewrite.** Replace `CitationPreservingRagRetrieval(VertexAiRagRetrieval)` with a plain `FunctionTool` wrapping `rag.async_retrieve_contexts`. Same regex pair, same return shape (`{"chunks": [...]}`), same `tool_context.state.setdefault("citations", [...])` pattern. Lose the parent class's `process_llm_request` (which we don't want anyway — that was the bug, not the feature).
> - **T8c rewrite.** `app._app_name` does not exist; use `app._tmpl_attrs["app_name"]`. The "stable app_name across factory rebuild" claim is informational only; the per-turn-replay strategy is what makes the design work.
> - **T9 rewrite (light).** Event discriminators derive from `content.role` + presence of `text`/`function_call`/`function_response` parts; not from a `kind` field. SSE-delta granularity may require an explicit `RunConfig` with `streaming_mode`; if not, deltas arrive in coarse chunks (acceptable for first pass).
> - **T7 unchanged.** Parallel dispatch confirmed — multi-question fan-out works as designed.
> - **`google-adk>=1.16,<2.0`** pinned and installed (resolved to **1.31.1**). Required transitive bumps: `fastapi 0.115.5 → 0.136.1`, `uvicorn 0.32.1 → 0.46.0`, `starlette 0.41.3 → 0.52.1`. Existing FastAPI app + 99 tests still load cleanly.

Sources:
- read-only Explore agent against `/home/lukasthomas/sleek-rag/`
- Context7 docs query against `/google/adk-python`
- earlier LangSmith chunk-shape research (`chunk-shape-from-langsmith.md`)
- 2026-04-30 deep research (`adk-factory-and-history.md`)

---

## A. Streaming + SSE integration

### A1. ADK event types (VERIFIED — T0 probe 1, 2026-04-30)

`google-adk` is **not installed** in `backend/.venv` and is **not in `backend/requirements.txt`**. The plan assumes `AdkApp.async_stream_query` emits events with a discriminable type (text-delta vs. tool-call vs. final-response) and an `author` field naming the emitting agent. This must be verified in T0 before T9 (SSE translator) can be written confidently.

What we need to confirm:
- partial-text streaming events for token-level delta forwarding,
- tool-call start/end events with tool name + args + result payloads,
- final-response event signaling end of run,
- `author` (agent name) on each event so we can distinguish orchestrator output from sub-agent output (sub-agent text should NOT be forwarded raw — only the orchestrator's final synthesized turn).

### A2. Frontend SSE contract (VERIFIED)

`components/app-shell.tsx:920-1025` parses these frame shapes today:

- `{type: "delta", content}` — append to streaming text
- `{type: "meta", citations, content}` — replace streaming text with annotated `[N]` version + render chips
- `{type: "done", message_id}` — finalize
- `{progress: {done, total, question}}` — projektanalyse-only progress
- `{delta}` — legacy fallback (line 1015)

Citation dedup + renumbering happens in `components/chat.tsx:114-152` via `useMemo`, BEFORE rendering. Order-independent: `meta` can arrive before, after, or interleaved with deltas.

`linkifyCitations` (`components/chat.tsx:76-82`) uses regex `\[(\d+)\]` to turn `[N]` into anchor links rendered as chips on click (`chat.tsx:183-201`).

**Implication**: We can keep the SSE contract identical. The only change: ADK runs are slower to first delta because the orchestrator (Pro) makes a routing decision before dispatching. Mitigation in T9 — emit a synthetic `delta` placeholder ("…") only if no real text within ~800ms (open question).

### A3. Parallel tool calls (VERIFIED — T0 probe 4, 2026-04-30: PARALLEL)

Whether ADK serializes or parallelizes when the LLM emits multiple `function_call` parts in one turn is the load-bearing question for multi-question fan-out. If ADK serializes, the orchestrator instruction must split the user message into separate sub-turns, which is harder to do reliably. If ADK parallelizes natively, the instruction is trivial.

Confirm in T0 by simulating "Was ist X und welche Y?" with two AgentTool sub-agents and inspecting timing.

---

## B. Session + state propagation

### B1. Initial session state (SUPERSEDED — strategy (c) replays Supabase rows per turn; see T0 probe 5 + 6 for the verified pattern)

`VertexAiSessionService.create_session(state={"corpus_name": ...})` accepting an initial state dict is the ADK pattern documented in tutorials, but we need to confirm:
- the dict is honored on creation,
- it survives across multiple `async_stream_query` calls within the same `session_id`,
- writes from inside FunctionTools (`tool_context.state["citations"] = ...`) persist to the same session.

### B2. ToolContext mutability (VERIFIED via Context7)

Per `/google/adk-python` docs:

> `tool_context.state` is dict-like; reads via `.get()`, writes via `state["key"] = value` are tracked as deltas and applied to the session.

Source: ADK docs "Access and Update Session State in Tools". Mutability in tools is the documented contract.

### B3. Post-run state read (VERIFIED — T0 probe 5, 2026-04-30)

After `app.async_stream_query` finishes, we need to read accumulated `citations` from session state. Approach: `await session_service.get_session(app_name, user_id, session_id)` then read `.state["citations"]`. Confirm this is the right API in T0.

---

## C. RAG retrieval API (VERIFIED)

### C1. Async variant — exists

`vertexai.preview.rag.async_retrieve_contexts` is exported. Path: `backend/.venv/lib/python3.10/site-packages/vertexai/preview/rag/__init__.py:166`. No `asyncio.to_thread` shim needed.

### C2. Response shape — confirmed

`backend/.venv/lib/python3.10/site-packages/vertexai/preview/rag/rag_retrieval.py:31-39`:

- returns `aiplatform_v1beta1.RetrieveContextsResponse`
- `response.contexts.contexts[*]` carries `text`, `source_uri`, `source_display_name`, `score`, plus `chunk` (RagChunk), `distance`, `sparse_distance`.

### C3. Per-call retrieval config — supported

`retrieval_query(..., rag_retrieval_config: Optional[resources.RagRetrievalConfig] = None)`. Override `top_k`, `filter`, `hybrid_search`, `ranking` at call time without touching the corpus.

---

## D. Existing wiring touch points (VERIFIED)

### D1. Pattern A v1 vs v2 — code paths still live

`backend/app/routers/chats.py:32-36` imports both `stream_projektanalyse` and `stream_projektanalyse_v2`. `chats.py:400-411` dispatches to either based on `fc.name`.

LangSmith research (`chunk-shape-from-langsmith.md` §6): zero recent v1 traces in production. Removing v1 from the tool tree in the ADK migration is **safe-pending-confirmation**: the only remaining risk is a frontend template that hardcodes `run_projektanalyse` (vs `_v2`). T11 must grep the frontend for this string before deletion.

### D2. Citation helpers — single-use, safe to delete after migration

`backend/app/citations.py`:
- `grounding_to_citations` — only call site `chats.py:440`
- `annotate_answer_with_refs` — only call site `chats.py:442`
- `_supports_to_char_offsets` — internal to `annotate_answer_with_refs`
- `_PAGE_RE`, `_FIGURE_RE` — moved into the new `retrieve_project_chunks` tool (one regex pair, isolated to retrieval boundary)

### D3. `vertex_rag_grounding.py` — single caller

`grounding_tool_for_project` only called at `chats.py:210`. Delete after migration.

### D4. Frontend `linkifyCitations` — order-independent (already noted in A2)

No frontend change required. Confirm in T12 manual smoke.

---

## E. Test infrastructure (VERIFIED)

### E1. Async setup

- No `conftest.py` in `backend/tests/`.
- `pytest-asyncio` is **not** in `backend/requirements.txt`.
- Existing tests use bare `asyncio.run()` (e.g. `test_chat_pattern_a_stream.py:140-149`).

T13 should follow the same pattern (no plugin churn) unless ADK testing fixtures require pytest-asyncio.

### E2. Existing chat-stream test pattern

`backend/tests/test_chat_pattern_a_stream.py:152-167` mocks via:
- `monkeypatch.setattr(chats_module, "supabase", lambda: stub)` (sync)
- `monkeypatch.setattr(chats_module, "_client", lambda: fake_client)` returning a fake with `.aio.chats.create`
- Fake chat session yields `_FakeChunk` objects with `.candidates[0].content.parts` shape
- SSE drained line-by-line, JSON-parsed (`test_chat_pattern_a_stream.py:140-149`)

For the ADK port, the equivalent monkeypatch target is the singleton `AdkApp` (T8). The fake should yield ADK Event-shaped objects. Exact event shape pending T0.

---

## F. Dependencies (VERIFIED)

### F1. google-adk — NOT installed

Must be added in T1. Latest stable as of writing: target `>=1.16,<2.0` (avoid the `2.0.0a1` alpha). Confirm in T0 spike.

### F2. vertexai — installed

`google-cloud-aiplatform[rag]>=1.108.0` (`backend/requirements.txt:15`). Preview RAG import path works.

### F3. Python — 3.10.12

`backend/.venv/pyvenv.cfg`. Python 3.10 EOL is 2026-10-04. Not a blocker for this plan but worth flagging in the master roadmap.

---

## G. Per-question fan-out (PARTIALLY VERIFIED — T0 2026-04-30)

### G1. AgentTool state inheritance

When `chat_orchestrator` calls `rag_specialist` via `agent_tool.AgentTool`, the sub-agent runs as its own LLM turn. We need to confirm the sub-flow inherits the parent session state — specifically, `tool_context.state["corpus_name"]` must be visible inside `retrieve_project_chunks` when it runs under the sub-agent's tool dispatch.

Verify in T0 by writing a probe FunctionTool that reads `state["corpus_name"]` from inside the `rag_specialist` invocation.

### G2. Billable turn count

Each AgentTool call is presumed to be one or more separate billable LLM turns (the sub-agent runs its own generation). N parallel rag_specialist calls = N Flash turns + 1 Pro orchestration turn. Cost-model implication: a multi-question turn is meaningfully more expensive than today's single-call Pattern A.

Confirm in T0 with token-usage telemetry.

---

## Open spike items (T0 must resolve before T1+)

1. ADK event types from `async_stream_query` (delta, tool_call_start/end, final, author field) — A1.
2. Parallel vs serialized tool dispatch when LLM emits multiple `function_call` parts — A3.
3. `VertexAiSessionService.create_session(state=...)` initial-state contract + persistence — B1.
4. `session_service.get_session(...).state` post-run read — B3.
5. AgentTool sub-agent inherits parent state — G1.
6. Token / billable-turn accounting per orchestrator turn — G2.

If any of these resolve unfavorably, the architecture changes:
- (1, 2) failure → fall back to a simpler sequential dispatch loop, lose multi-question speedup.
- (3, 5) failure → switch to per-project `AdkApp` factory with cache (the "factory" alternative we explicitly rejected for quality reasons in the design discussion).
- (4) failure → emit citations via custom event side-channel from the FunctionTool itself (yield-style) instead of state aggregation.
