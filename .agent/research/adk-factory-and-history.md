# ADK per-corpus factory + history strategy — research findings

Date: 2026-04-30
Scope: switching from a single shared `AdkApp` (state-based corpus injection)
to a per-corpus `AdkApp` factory using Google's managed `VertexAiRagRetrieval`
tool. Ground-truth answers needed before updating the migration plan.

Source conventions:
- C7 = Context7 `/google/adk-python` (mirror of `llms-full.txt` from
  https://github.com/google/adk-python/blob/main/llms-full.txt).
- ADK source = direct GitHub fetch
  https://github.com/google/adk-python/blob/main/src/...
- Vertex AI agent_engines = direct GitHub fetch
  https://github.com/googleapis/python-aiplatform/blob/main/vertexai/agent_engines/...
- Google Cloud docs = docs.cloud.google.com
- "UNVERIFIED" = doc not conclusive; flagged inline.

---

## Group 1 — `after_tool_callback` for citation extraction

### 1.1 Exact signature on `LlmAgent`

ANSWER (verified). The callback signature is:

```python
def simple_after_tool_modifier(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
    tool_response: Dict
) -> Optional[Dict]:
```

Return-value semantics, quoted verbatim from C7:

> "If the callback returns a new dictionary, this dictionary **replaces** the
> original `tool_response`. This mechanism enables modifying or filtering the
> result that the LLM ultimately receives."
>
> "If the callback returns `None` (or a `Maybe.empty()` object in Java), the
> original `tool_response` is used without any changes."

Source: C7 `/google/adk-python` "After Tool Callback > Return Value Effect"
(github.com/google/adk-python/blob/main/llms-full.txt).

The callback may also be `async`. Imports needed:
`from google.adk.tools.base_tool import BaseTool`,
`from google.adk.tools.tool_context import ToolContext`.

### 1.2 Is `tool_context.state` writable inside the callback, with persistence?

ANSWER (verified). Yes. The same `ToolContext` is passed to the callback as
to `FunctionTool.run_async`, and writes to `tool_context.state` produce a
state delta that is committed to the session by the runner — same mechanism
as inside a tool body.

Quote from C7 (`Accessing Session State and Artifacts with ToolContext`):

> "Use ToolContext to manage session state, save/load artifacts, and search
> memory within a tool. It provides access to session_id, user_id, and state
> dictionaries."
>
> ```python
> tool_context.state["visits"] = visit_count + 1
> ```

Source: https://context7.com/google/adk-python/llms.txt (ToolContext section).

So in our citation flow we can do:

```python
async def capture_citations(tool, args, tool_context, tool_response):
    if tool.name != RAG_TOOL_NAME:
        return None
    citations = tool_context.state.get("citations", [])
    citations.extend(_extract(tool_response))
    tool_context.state["citations"] = citations
    return None  # don't mutate the LLM-visible tool_response
```

UNVERIFIED edge case: whether writes survive when the callback returns a
*new* dict vs `None`. The state-delta mechanism is independent of return
value, so writes should commit either way; we should cover this with a
test.

### 1.3 What does `tool_response` look like for `VertexAiRagRetrieval`?

ANSWER (verified — important deviation from the plan's assumption).

The `tool_response` is **not** the raw `RetrieveContextsResponse` and **not**
a list of context dicts with metadata. It is what `run_async` returns,
which is one of:

- a string `"No matching result found with the config: <vertex_rag_store>"`
  when no contexts came back, OR
- a Python `list[str]` — `[context.text for context in
  response.contexts.contexts]` — i.e. the bare passage texts, with
  `source_uri`, `score`, `chunk_id` and any other metadata DISCARDED.

Source (GitHub raw,
https://github.com/google/adk-python/blob/main/src/google/adk/tools/retrieval/vertex_ai_rag_retrieval.py
lines 89–111):

```python
async def run_async(
    self,
    *,
    args: dict[str, Any],
    tool_context: ToolContext,
) -> Any:
    from ...dependencies.vertexai import rag
    response = rag.retrieval_query(
        text=args['query'],
        rag_resources=self.vertex_rag_store.rag_resources,
        rag_corpora=self.vertex_rag_store.rag_corpora,
        similarity_top_k=self.vertex_rag_store.similarity_top_k,
        vector_distance_threshold=self.vertex_rag_store.vector_distance_threshold,
    )
    logging.debug('RAG raw response: %s', response)
    return (
        f'No matching result found with the config: {self.vertex_rag_store}'
        if not response.contexts.contexts
        else [context.text for context in response.contexts.contexts]
    )
```

CONSEQUENCE for the plan:

The managed `VertexAiRagRetrieval` tool, as currently shipped, **cannot be
the source of citations in our chat UI** — we'd lose `source_uri`,
`document_id`, `page_number`, `score`, etc. Options:

1. **Subclass** `VertexAiRagRetrieval` and override `run_async` to preserve
   metadata. Minimal change — drop in a subclass like
   `CitationPreservingRagRetrieval` whose `run_async` returns
   `{"contexts": [{"text": c.text, "source_uri": c.source_uri,
   "score": c.score, ...}, ...]}`.
2. **Don't use the managed tool**; wrap our own `FunctionTool` around
   `vertexai.preview.rag.retrieve_contexts`. That gives us full control
   but loses Gemini-native grounding metadata when ADK hands the tool to
   Gemini 2+ as a built-in `Retrieval` (see 1.4 next).
3. Combine: keep the managed tool for the LLM (so `process_llm_request`
   inserts native grounding) **and** also call a side-channel
   `FunctionTool` we own for citation extraction. Wasteful — two RAG
   calls per turn.

Recommendation: option (1). Subclass and override `run_async`.

### 1.4 Can the callback distinguish which tool just ran?

ANSWER (verified). Yes. `tool.name` and/or `tool_context.agent_name` are
both in scope. From the C7 example:

```python
agent_name = tool_context.agent_name
tool_name = tool.name
print(f"[Callback] After tool call for tool '{tool_name}' in agent '{agent_name}'")
...
if tool_name == 'get_capital_city' and ...
```

So `if tool.name == "VertexAiRagRetrieval": ...` (or whatever `name=` we
pass at construction) is the canonical filter.

Caveat: GitHub issue google/adk-python#2629 ("Before tool callback isn't
called when using VertexAiRagRetrieval as tool for the LlmAgent") suggests
the **before**-tool callback may not fire for `VertexAiRagRetrieval`
specifically because the tool is partly executed via Gemini's native
`Retrieval` declaration in `process_llm_request`. UNVERIFIED whether the
**after**-tool callback fires for the managed tool — we MUST test this
end-to-end before committing to the design. If it doesn't fire, we are
forced into option (2) above (own FunctionTool).

Source: https://github.com/google/adk-python/issues/2629 (titled "Before
tool callback isn't called when using VertexAiRagRetrieval as tool for
the LlmAgent").

### 1.5 Concrete docs example of `after_tool_callback` mutating state

Quoted in 1.1/1.4 above (C7 `simple_after_tool_modifier`); the example
mutates `tool_response` rather than `tool_context.state`, but it is the
canonical reference. For state mutation specifically, the "comprehensive
tool" snippet (1.2) shows the same `tool_context.state[...] = ...`
pattern that works inside callbacks.

---

## Group 2 — Conversation history persistence (the critical question)

### 2.1 Session services and persistence guarantees

Quoted from C7 ("SessionService Implementations" section):

| Service | Persistence | Backing store |
|---|---|---|
| `InMemorySessionService` | Process-local. **Lost on restart.** | Python dicts in app memory. "It offers no persistence, meaning all conversation data is lost if the application restarts." |
| `DatabaseSessionService` | Reliable, self-managed. | SQLAlchemy. Supports SQLite, PostgreSQL, MySQL, MariaDB. "Connects to a relational database, such as PostgreSQL, MySQL, or SQLite, to store session data persistently in tables. Data survives application restarts." |
| `VertexAiSessionService` | Managed by Google. | Vertex AI Agent Engine Sessions (GCP API). Requires a project, location, and an Agent Engine resource ID. |

`DatabaseSessionService` requires an **async** SQLAlchemy driver from
ADK Python v1.22+ (e.g. `postgresql+asyncpg://`,
`sqlite+aiosqlite:///./my_agent_data.db`). The schema migrated at v1.22
(see "DatabaseSessionService" section in C7).

DatabaseSessionService schema (V1, from
https://github.com/google/adk-python/blob/main/src/google/adk/sessions/database_session_service.py):

- `StorageSession` (PK: `app_name`, `user_id`, `id`; columns: `state`,
  `create_time`, `update_time`).
- `StorageEvent` (cols: `id`, `app_name`, `session_id`, `user_id`,
  `timestamp`, plus content/state-delta/actions blobs).
- `StorageAppState` (PK: `app_name`).
- `StorageUserState` (PK: `app_name`, `user_id`).
- `StorageMetadata` (key/value).

This is **not** compatible with our existing Supabase `chat_messages`
table; ADK wants its own schema, owned by ADK migrations. We could not
"reuse" the table — we'd be running ADK's tables in parallel.

UNVERIFIED: exact column types for `StorageEvent.content` (likely `JSON`
or `BLOB`). Not load-bearing for the decision.

Other implementations (search shows none in the main repo):

- No Redis session service in mainline `google.adk.sessions`.
- No Spanner session service in mainline.
- A `VertexAiSessionService` express-mode variant exists (lighter auth,
  same API) per the ADK express-mode docs.

### 2.2 How does ADK assemble history when we re-use a `session_id`?

ANSWER (verified). On every `run_async`/`async_stream_query` invocation
with an existing `session_id`, the runner:

1. Calls `session_service.get_session(...)` to load all prior `Event`s
   for that session.
2. Appends the new user message as a new event (via
   `_append_new_message_to_session` → `session_service.append_event`).
3. Builds the LLM request `contents` by walking
   `session.events` and converting each event's `event.content` to
   `types.Content`. The relevant code is `_get_contents` in
   `src/google/adk/flows/llm_flows/contents.py`:

```python
def _get_contents(
    current_branch: Optional[str],
    events: list[Event],
    agent_name: str = '',
    *,
    preserve_function_call_ids: bool = False,
) -> list[types.Content]:
    ...
    for event in result_events:
        content = copy.deepcopy(event.content)
        if content:
            if not preserve_function_call_ids:
                remove_client_function_call_id(content)
            contents.append(content)
```

So **history replay is automatic** as long as the same `session_id` is
passed to the same `app_name`. Quoted from C7
(github.com/google/adk-python/blob/main/AGENTS.md "How the Runner Works
> Invocation Lifecycle"):

> "Upon receiving another message from the user, a new `run_async()`
> invocation is initiated. This restarts the cycle by loading the
> session, which now incorporates all events from the preceding turns,
> ensuring continuity."

### 2.3 Cold-start question (process restart, empty LRU cache)

ANSWER (verified, conditional on session service choice):

- With `InMemorySessionService`: **history is lost.** The dict lives in
  the dead process.
- With `VertexAiSessionService`: **history is preserved.** Sessions are
  stored in Google's managed Agent Engine Sessions store, fetched by
  API. A fresh `AdkApp` constructed with the same `agent_engine_id` will
  see the same sessions.
- With `DatabaseSessionService` against a persistent DB: **history is
  preserved**, fetched from the DB by `session_id`.

So the "cold start" question reduces to "did we pick a persistent
session service?". `InMemorySessionService` is unsafe for any backend
that scales horizontally or restarts.

### 2.4 AdkApp eviction — do session_ids survive?

ANSWER (verified). Yes for persistent session services. Sessions are
keyed by `(app_name, user_id, session_id)`, **not** by an `AdkApp`
instance pointer. As long as the rebuilt `AdkApp` is constructed with
the same `app_name` and pointed at the same session backend, existing
`session_id`s keep working.

For `VertexAiSessionService`, `app_name` is the Reasoning Engine
resource name, e.g.
`projects/<p>/locations/<l>/reasoningEngines/<id>`. So all our
per-corpus `AdkApp`s should share the same `agent_engine_id` if we want
session continuity across rebuilds — which is the correct design (one
empty Agent Engine instance per environment, used purely as a session
namespace).

For `DatabaseSessionService`, `app_name` can be any string. Use a stable
constant like `"sleek-rag"` (NOT the corpus ID) so rebuilding an
`AdkApp` for corpus X still finds session C's history.

Note: this implies `app_name` is **not** how we route per-corpus
behaviour. The corpus binding lives in the `VertexAiRagRetrieval` tool
attached to the agent inside the `AdkApp`, not in `app_name`.

### 2.5 Source-of-truth tradeoffs

#### Vertex AI Agent Engine Sessions

- Retention: default expiration TTL of **365 days** if neither
  `expire_time` nor `ttl` is specified at session creation; can be set
  shorter or longer per session. UNVERIFIED — Google's docs page for
  Sessions kept being summarised by the WebFetch model rather than
  quoted, but multiple search results converge on 365 days as the
  default. We should re-confirm by hitting the API and reading the
  returned session record's `expire_time` field.
  Source (best available): WebSearch hit summarising
  https://docs.cloud.google.com/agent-builder/agent-engine/sessions/manage-sessions-api
  ("If neither expiration time nor time to live (TTL) is specified, the
  system applies a default TTL of 365 days").
- Pricing (from January 28, 2026): **$0.25 per 1,000 stored session
  events or memories.** Each user turn + each agent reply + each tool
  call/response is an event, so a 10-turn chat with one tool call per
  turn is ~30 events ≈ $0.0000075. Practically negligible at sleek's
  expected scale.
  Source: cloud.google.com/vertex-ai/pricing (via WebSearch).
- Independence from a deployed Reasoning Engine: an **empty Agent Engine
  instance** is sufficient. You don't need to deploy your agent to
  Agent Engine to use Sessions. From the Medium article "Lightweight
  Session State: Using Vertex AI's Session Management Without a Full
  Agent Deployment" — create an Agent Engine via
  `agent_engines.create()` (no agent code) and use the returned
  `agent_engine_id` with `VertexAiSessionService`.
- Display/query: sessions are listable via
  `AdkApp.async_list_sessions(user_id=...)` and individually fetchable
  via `async_get_session(...)`. Events are returned as JSON dicts. So
  yes, we can build our chat-list UI on top — but at the cost of a GCP
  API call instead of a local DB read.

#### DatabaseSessionService against Supabase Postgres

- Schema: ADK-owned, NOT compatible with our existing `chat_messages`.
  We would either (a) drop our table and migrate the UI to read ADK's
  `StorageEvent` rows or (b) duplicate writes (one to our table, one to
  ADK's).
- Cost: included in our existing Supabase plan, no per-event charge.
- ADK's schema migrated at v1.22 — so we must own DB migrations
  (`adk migrate` or pin to a specific schema version).

#### Keep Supabase `chat_messages`, replay every turn

- Cost: zero new infra. Same as today.
- LLM cost: history replay sends the full transcript every turn; same
  as today (we're already doing this).
- ADK semantic cost: an in-memory session per turn means ADK can't
  benefit from its own context-cache features (cache_intervals,
  ttl_seconds), tool call/response pairing across turns is opaque to
  ADK (we'd have to encode tool calls as plain text in history).
- Implementation cost: every turn we'd build a fresh
  `InMemorySessionService`, `runner.session_service.create_session(...)`
  with our reconstructed events list. The "create session with custom
  initial events" pattern is supported via
  `app.async_stream_query(..., session_events=...)` per the
  v1.135-vintage docs (the `session_events` parameter exists in
  `async_stream_query`'s signature in older versions; UNVERIFIED in
  current v1.144 — search results showed it in the v1.135 reference but
  not the latest).

### 2.6 Stateless FastAPI / Cloud Run pattern

GitHub issue google/adk-python#742 (and follow-ups) explicitly document
this case: deploy your code to Cloud Run, pass
`--agent_engine_id=<empty-agent-engine>` to `adk deploy cloud_run` (or
construct `VertexAiSessionService(project=..., location=...,
agent_engine_id=...)` manually), and Cloud Run handles the runtime
while Vertex handles the sessions. This is the canonical pattern for
"stateless backend + managed sessions". Multiple Medium tutorials
("Lightweight Session State..." and "Manage your Agent User Sessions
with ADK and Vertex AI Memory Engine") describe the same pattern.

Sources:
- https://github.com/google/adk-docs/issues/742
- https://medium.com/google-cloud/lightweight-session-state-using-vertex-ais-session-management-without-a-full-agent-deployment-af167bbbc56f

---

## Group 3 — `VertexAiRagRetrieval` managed tool

### 3.1 Constructor parameters

ANSWER (verified). From the source file
`src/google/adk/tools/retrieval/vertex_ai_rag_retrieval.py` lines 54–63:

```python
def __init__(
    self,
    *,
    name: str,
    description: str,
    rag_corpora: list[str] = None,
    rag_resources: list[rag.RagResource] = None,
    similarity_top_k: int = None,
    vector_distance_threshold: float = None,
):
```

So:
- `rag_resources=[RagResource(rag_corpus=...)]` ✓ supported.
- `similarity_top_k` ✓ exposed as a flat constructor param.
- `vector_distance_threshold` ✓ exposed as a flat constructor param.
- `rag_retrieval_config` — **NOT** a constructor param. The richer
  config object (`top_k`, `filter.metadata_filter`,
  `filter.vector_distance_threshold`, `hybrid_search`) exists in the
  underlying RAG SDK (`vertexai.preview.rag`), but the ADK wrapper
  flattens only `similarity_top_k` and `vector_distance_threshold`.
- No `metadata_filter`, no `hybrid_search`, no `rag_retrieval_config`
  passthrough. If we need them, we must either (a) subclass and pass
  the richer config to `rag.retrieval_query`, or (b) wait for an ADK
  release that exposes it.

Source: GitHub raw URL above + WebSearch confirmation.

### 3.2 What does `run_async()` return as tool result?

ANSWER (verified, see 1.3 above). Either:
- `str`: `"No matching result found with the config: <vertex_rag_store>"`, or
- `list[str]`: bare context texts.

This is what `tool_response` will be inside `after_tool_callback`. It is
**not** a normalized list of contexts with metadata. **Citation
extraction from `tool_response` is impossible without subclassing.**

### 3.3 Async retrieval path

ANSWER (PARTIALLY VERIFIED). The ADK wrapper calls
`rag.retrieval_query(...)` synchronously inside an `async def
run_async`, with no `asyncio.to_thread` or async variant. So the call is
blocking on the event loop. The SDK does have `async_retrieve_contexts`
in `vertexai.preview.rag`, but the ADK wrapper does **not** use it.

Source: same source file, line 99 (`response = rag.retrieval_query(...)`
is a plain synchronous call inside an `async def`).

CONSEQUENCE: under load, RAG calls will starve the event loop. For our
single-user-per-request FastAPI pattern this is mostly fine, but worth
flagging. A subclass that uses `async_retrieve_contexts` (or wraps in
`asyncio.to_thread`) is a small, mechanical fix.

---

## Group 4 — Factory + LRU cache pattern

### 4.1 Documented "preferred" caching pattern for per-tenant `AdkApp`?

ANSWER (no canonical doc). Searches turn up no official ADK
recommendation for caching `AdkApp` per tenant/corpus/project. The
reference deployment story is "one `AdkApp` per deployed Agent Engine,
many tenants share it via per-user sessions". Our per-corpus split is
slightly off the happy path.

Practical guidance synthesised from the source:

- `AdkApp.__init__` is cheap by itself — it stores references; the
  expensive work happens lazily in `set_up()` / `_init_session()`. So
  rebuilding on cache miss is acceptable.
- Concurrency safety of `functools.lru_cache`: not safe for `async`
  factory functions and not bounded by memory; use
  `cachetools.LRUCache` + a lock, or a small homemade dict + `asyncio.Lock`
  per-key, to avoid two coroutines building the same `AdkApp`
  simultaneously.
- Eviction-during-use risk: in-flight request holds a reference to the
  evicted `AdkApp` object; Python won't GC it until all callers
  release. So eviction is always safe, just memory-pressure-delayed.

UNVERIFIED: whether holding stale references causes connection-pool
exhaustion (each `AdkApp` may hold a Vertex client). Probably fine but
worth load-testing.

### 4.2 Teardown / cleanup steps

ANSWER (no documented teardown). Searches and the C7 docs show no
`close()` / `aclose()` / `shutdown()` method on `AdkApp`. The class
relies on Python GC. If we drop a reference during eviction, the
underlying Vertex client and session-service handle should be GC'd
normally. No explicit cleanup is required in the ADK contract.

UNVERIFIED: a `set_up()` method exists (visible in
`vertexai/agent_engines/templates/adk.py`), but no symmetric
`tear_down()`. If memory leaks turn up in production, this is the area
to instrument.

### 4.3 Cold-start cost — `AdkApp(...)` construction time

ANSWER (NO PUBLISHED BENCHMARK). No documented benchmark for AdkApp
construction time as a function of agent-tree size. Order-of-magnitude
estimate from reading the source: `__init__` is ~O(ms) (just argument
binding); first invocation triggers `set_up()` which initializes the
default session/artifact services (in-memory dict allocations, not
network calls if we provide our own `session_service_builder`). The
expensive call is the first request to Gemini (network). So cache-miss
cost ≈ "one extra cold network call on first use" + a few ms of Python
object init. Acceptable for an LRU.

If we want a hard number, we should benchmark with a 4-agent tree and
add it to `.agent/benchmarks/`.

---

## Group 5 — Per-project agent specialization (future-looking)

### 5.1 Factory flexibility for per-project variation

ANSWER (verified, simple). Yes — the factory pattern is naturally
flexible. Inside the factory function we can:

- Read project metadata from Supabase (e.g. `projects.system_prompt`,
  `projects.extra_tools[]`) at AdkApp build time.
- Construct the `LlmAgent(instruction=..., tools=[...])` parametrised
  per project.
- Cache the result keyed by `(corpus_id, project_metadata_version)` so
  metadata changes invalidate the cache.

The `AdkApp` is a regular Python object, so any logic we put in the
factory is valid. The only constraint is that `app_name` (used by
`VertexAiSessionService` for session lookup) should remain stable
across factory invocations for the same logical app — see 2.4.

---

## Recommended history strategy

**Recommendation: (a) `VertexAiSessionService` against an empty Agent
Engine instance, OR (c) keep Supabase as source of truth and replay
into a fresh `InMemorySessionService` each turn. Lean (a) for the
managed-future story; (c) is the safer incremental step.**

Rationale:

| Criterion | (a) Vertex sessions | (b) DatabaseSessionService → Supabase | (c) Supabase + replay |
|---|---|---|---|
| Schema we own | No (Vertex managed) | No (ADK-defined tables) | Yes (existing `chat_messages`) |
| Compatible with current UI (chat list, history view) | Needs new fetch via Agent Engine API | Needs UI rewrite to read ADK schema | Works as-is |
| Cold-start safety | ✓ persistent | ✓ persistent | ✓ (we own it) |
| Multi-turn fidelity (tool calls, function-call pairing) | ✓ ADK-native event model | ✓ ADK-native event model | ✗ we'd serialise tool turns to plain text |
| Cost | ~$0.25/1k events ≈ negligible | $0 marginal | $0 marginal |
| ADK upgrade risk | Low (managed by Google) | High — schema migrated at v1.22 already | Low (we don't depend on ADK schemas) |
| Required new infra | One empty Agent Engine instance per env | Schema migration in Supabase | None |

**Why (b) is rejected:** ADK's schema is its own, the v1.22 migration
proves it will keep changing, and we'd be locked into running ADK
migrations against our Postgres. The "share the table" ergonomic
promise doesn't hold up — the schemas don't match.

**Why (a) wins long-term:** lets ADK manage tool-call/response pairing,
unlocks future ADK features (context cache, plugins, memory bank), and
the cost is a rounding error. Requires us to expose chat history via
`AdkApp.async_list_sessions` rather than a Supabase select; small UI
adapter cost.

**Why (c) is the safer step-1:** zero schema migration, zero new GCP
resource, no UI changes. We pay the multi-turn-fidelity tax (tool calls
become opaque text in replayed history) but we already pay that today.
Lets us land the per-corpus factory + grounding in one PR without also
swapping our chat persistence model.

**Concrete proposal:** ship (c) as part of the per-corpus factory PR,
and open a follow-up issue to migrate to (a) once the factory pattern
is validated in production. Migration from (c)→(a) is mechanical:
swap `session_service_builder` from `lambda: InMemorySessionService()`
to `lambda: VertexAiSessionService(project, location, agent_engine_id)`,
backfill prior `chat_messages` rows by replaying them into Vertex
sessions one-time (or accept that pre-migration history stays in
Supabase-only).

### Plan changes that this research forces

1. **Citations: subclass `VertexAiRagRetrieval`.** The managed tool's
   `run_async` discards metadata (returns `list[str]`). Without a
   subclass we cannot extract `source_uri` / `page_number` /
   `document_id` / `score` for our citation chips. Plan must include a
   `CitationPreservingRagRetrieval` subclass step.

2. **Verify `after_tool_callback` actually fires for `VertexAiRagRetrieval`.**
   Issue #2629 reports the *before* callback doesn't fire for this
   tool because `process_llm_request` partially merges it into Gemini's
   native `Retrieval`. UNVERIFIED for `after_tool_callback` — must be
   tested first; if it doesn't fire, fall back to a plain `FunctionTool`
   that wraps `rag.retrieval_query` and forget the managed tool entirely.

3. **Pick a session strategy explicitly.** Plan's "factory pattern"
   silently assumes `InMemorySessionService` (process-local). That
   breaks horizontal scaling. Pin the choice to (c) with a written
   commitment to migrate to (a) later.

4. **Stable `app_name` across the factory.** All per-corpus `AdkApp`s
   must share `app_name` (e.g. `"sleek-rag"`) so sessions remain
   findable across factory rebuilds. The corpus binding lives in the
   tool, not in `app_name`.

5. **No `rag_retrieval_config` available.** Drop any plan items that
   assume metadata filtering or hybrid_search via the ADK tool — those
   require either subclassing or moving to a custom `FunctionTool`.

6. **RAG call is sync-on-async.** `rag.retrieval_query` blocks the
   event loop inside the ADK wrapper. Either (a) accept the latency,
   (b) subclass to call `async_retrieve_contexts`, or (c) wrap in
   `asyncio.to_thread`. Add a perf note in the plan.
