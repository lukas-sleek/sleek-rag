# 3. Gemini 3.1 + serverless RAG: `grounding_metadata` is intermittently None

**This is the important one — a model/API limitation, not our bug.**

`rag_specialist` (`adk/agents.py:178`, model `gemini-3.1-pro-preview`) uses
**`VertexAiRagRetrieval` as server-side managed grounding** (`Tool(retrieval=…)`).
The retrieved chunks reach us **only** via `event.grounding_metadata` — there is
no client-side `function_call`/`function_response` carrying them.

**What we observed** (via temporary `GM-DIAG` logging, since removed):
- One Projektanalyse run: `gm=True gm_chunks=10 gm_supports=9` — chunks fully
  present, with real `retrieved_context.text` + `uri` (the GCS PDF paths).
- Another run, same questions: `gm=False` — `grounding_metadata` entirely
  `None`. No function_response either.
- → **intermittent**: present one run, absent the next, same question.

**Why** (web research):
- Google's RAG-output doc: for **Gemini 2.5 and later, `grounding_chunks` is
  empty and should be ignored** (was populated for ≤2.0). Retrieval output moved
  to `retrieved_context` / `citation_metadata` / `rag.retrieval_query`.
- **python-genai issue #2120** (open p2 bug, filed by Google, Mar 2026):
  `gemini-3.1-*-preview` returns empty/null `grounding_chunks` even when
  `grounding_supports` has data, while `gemini-3-flash-preview` and
  `gemini-2.5` still return chunks. API/protobuf-level, affects all SDKs.
  **No official fix, no workaround except using an older model.**
  https://github.com/googleapis/python-genai/issues/2120

**Conclusion:** when `gm` is present, our existing extractor
(`gm.grounding_chunks[].retrieved_context.text/uri` in
`streaming_agent_tool.py` + `projektanalyse_tool.py`) is **already correct**.
When `gm` is None there is **no alternate field** to read — the data simply
isn't in the response. So "adapt the extractor" cannot recover it.

**Decision (user): ACCEPT THE INTERMITTENCY.** No model swap, no client-side
retrieval rewrite. The finding-2 activity-panel fix already makes the UX honest:
chunks show when `gm` is present, "Keine grundenden Treffer" when it isn't.

**Caveat:** inline `[N]` citations + the citation list are built from the same
`grounding_chunks`, so they also degrade on the runs where `gm` is None.

**If revisited later**, the only robust ways to guarantee chunks while keeping
3.1 for generation are:
- **Client-side retrieval** — call `rag.retrieval_query`/`retrieve_contexts`
  ourselves per question; chunks come back in the tool response, independent of
  the flaky `grounding_metadata`. (Bigger change; there's prior art — plan 19.0
  briefly used a FunctionTool wrapping `rag.async_retrieve_contexts`.)
- **Pin `rag_specialist` to `gemini-2.5-flash`** (`config.gemini_chat_model` is
  already that) — per #2120, 2.5 returns chunks reliably.

Sources:
- https://docs.cloud.google.com/vertex-ai/generative-ai/docs/model-reference/rag-output-explained
- https://docs.cloud.google.com/vertex-ai/generative-ai/docs/reference/rest/v1beta1/GroundingMetadata
- https://github.com/googleapis/python-genai/issues/2120
