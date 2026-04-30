# ADK migration — pre-plan assumption check

Companion to the planned ADK multi-agent chat replacement. Notes which architectural assumptions are verified against the current codebase / installed deps, and which need a small spike before plan tasks commit to them.

> **2026-04-30 update — architecture pivoted.** The original sketch in this doc assumed a single shared `AdkApp` with state-based corpus injection via a custom `FunctionTool`. That has been superseded by a **per-corpus `AdkApp` factory** using a subclassed `VertexAiRagRetrieval` tool. Most of the unverified items below have been answered by the deeper research in [`adk-factory-and-history.md`](adk-factory-and-history.md), which is now the load-bearing reference. This file is kept for traceability of how the design evolved.
>
> **Surviving open spike items** (must resolve in plan T0 before the factory ships):
> 1. Does `after_tool_callback` fire for `VertexAiRagRetrieval`? (Issue #2629 confirms `before` doesn't.) — research suggests we sidestep this entirely by capturing citations inside the subclass's overridden `run_async`, but verify before committing.
> 2. ADK event types and `author` field on `async_stream_query` — needed to wire SSE translation correctly.
> 3. Whether multiple `function_call` parts in one turn execute in parallel or serialize — load-bearing for multi-question fan-out.

Sources:
- read-only Explore agent against `/home/lukasthomas/sleek-rag/`
- Context7 docs query against `/google/adk-python`
- earlier LangSmith chunk-shape research (`chunk-shape-from-langsmith.md`)
- 2026-04-30 deep research (`adk-factory-and-history.md`)

---

## A. Streaming + SSE integration

### A1. ADK event types (UNVERIFIED — spike required)

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

### A3. Parallel tool calls (UNVERIFIED — spike required)

Whether ADK serializes or parallelizes when the LLM emits multiple `function_call` parts in one turn is the load-bearing question for multi-question fan-out. If ADK serializes, the orchestrator instruction must split the user message into separate sub-turns, which is harder to do reliably. If ADK parallelizes natively, the instruction is trivial.

Confirm in T0 by simulating "Was ist X und welche Y?" with two AgentTool sub-agents and inspecting timing.

---

## B. Session + state propagation

### B1. Initial session state (UNVERIFIED — spike required)

`VertexAiSessionService.create_session(state={"corpus_name": ...})` accepting an initial state dict is the ADK pattern documented in tutorials, but we need to confirm:
- the dict is honored on creation,
- it survives across multiple `async_stream_query` calls within the same `session_id`,
- writes from inside FunctionTools (`tool_context.state["citations"] = ...`) persist to the same session.

### B2. ToolContext mutability (VERIFIED via Context7)

Per `/google/adk-python` docs:

> `tool_context.state` is dict-like; reads via `.get()`, writes via `state["key"] = value` are tracked as deltas and applied to the session.

Source: ADK docs "Access and Update Session State in Tools". Mutability in tools is the documented contract.

### B3. Post-run state read (UNVERIFIED — spike required)

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

## G. Per-question fan-out (UNVERIFIED — spike required)

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
