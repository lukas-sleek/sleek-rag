"""AgentTool variant that forwards sub-agent activity up to the parent.

The stock `google.adk.tools.agent_tool.AgentTool` runs the wrapped sub-agent
in its own internal Runner and returns only the merged non-thought final
text (see upstream `AgentTool.run_async`, lines 270-274 in v1.x). Thought
parts AND nested tool_calls / tool_responses are dropped on the floor —
they never reach the parent app's `async_stream_query` stream and therefore
never reach our debug activity panel. The activity panel ends up with two
unexplained `denkt laut` rows in a row (planning + post-retrieval analysis)
with no visible tool call between them — confusing UX.

This subclass keeps the same control flow but, while iterating sub-events,
appends a structured entry to `tool_context.state["agent_trace"]` for each
sub-event we want to surface:
    {"agent": <author>, "kind": "model_thought" | "tool_call" | "tool_response",
     "seq": <int>, ...kind-specific fields}
Because state writes propagate through the sub-event loop into the parent's
tool_response event as a `state_delta`, the parent SSE handler can read
them out and emit equivalent activity-panel frames in order.

Why state instead of events: AgentTool intentionally hides sub-events; the
parent runner only ever sees the synthesised tool_response. State, by
contrast, is forwarded by design (the upstream loop already does
`tool_context.state.update(event.actions.state_delta)` on every sub-event),
so this is the path of least resistance.
"""
from __future__ import annotations

from typing import Any

from google.adk.tools._forwarding_artifact_service import ForwardingArtifactService
from google.adk.tools.agent_tool import AgentTool, _get_input_schema, _get_output_schema
from google.adk.tools.tool_context import ToolContext
from google.adk.utils._schema_utils import validate_schema
from google.adk.utils.context_utils import Aclosing
from google.genai import types
from typing_extensions import override


class StreamingAgentTool(AgentTool):
    """AgentTool that captures sub-agent chain-of-thought into parent state.

    Drop-in replacement for `agent_tool.AgentTool`. The captured thought
    entries take the shape:
        {"agent": <sub-agent-name>, "text": <thought-text>, "seq": <int>}
    and are appended to `tool_context.state["agent_thoughts"]` (a list).
    The parent SSE handler in chats.py de-duplicates by `seq` so each
    thought is rendered exactly once even across nested AgentTool layers.
    """

    @override
    async def run_async(
        self,
        *,
        args: dict[str, Any],
        tool_context: ToolContext,
    ) -> Any:
        from google.adk.runners import Runner
        from google.adk.sessions.in_memory_session_service import (
            InMemorySessionService,
        )
        from google.adk.memory.in_memory_memory_service import InMemoryMemoryService

        if self.skip_summarization:
            tool_context.actions.skip_summarization = True

        input_schema = _get_input_schema(self.agent)
        if input_schema:
            input_value = input_schema.model_validate(args)
            content = types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=input_value.model_dump_json(exclude_none=True)
                    )
                ],
            )
        else:
            content = types.Content(
                role="user",
                parts=[types.Part.from_text(text=args["request"])],
            )
        invocation_context = tool_context._invocation_context
        parent_app_name = (
            invocation_context.app_name if invocation_context else None
        )
        child_app_name = parent_app_name or self.agent.name
        plugins = (
            tool_context._invocation_context.plugin_manager.plugins
            if self.include_plugins
            else None
        )
        runner = Runner(
            app_name=child_app_name,
            agent=self.agent,
            artifact_service=ForwardingArtifactService(tool_context),
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
            credential_service=tool_context._invocation_context.credential_service,
            plugins=plugins,
        )

        state_dict = {
            k: v
            for k, v in tool_context.state.to_dict().items()
            if not k.startswith("_adk")
        }
        session = await runner.session_service.create_session(
            app_name=child_app_name,
            user_id=tool_context._invocation_context.user_id,
            state=state_dict,
        )

        last_content = None
        last_grounding_metadata = None

        async with Aclosing(
            runner.run_async(
                user_id=session.user_id,
                session_id=session.id,
                new_message=content,
            )
        ) as agen:
            async for event in agen:
                if event.actions.state_delta:
                    tool_context.state.update(event.actions.state_delta)
                if event.content:
                    last_content = event.content
                    last_grounding_metadata = event.grounding_metadata
                # --- activity capture (the only addition over upstream) ---
                # Append thought / tool_call / tool_response entries from this
                # sub-event to parent state so the activity panel can render
                # the full nested chain in order.
                self._capture_activity(event, tool_context)

        await runner.close()

        if last_content is None or last_content.parts is None:
            return ""
        merged_text = "\n".join(
            p.text for p in last_content.parts if p.text and not p.thought
        )

        # Native vertex_rag_store retrieval doesn't make the model emit
        # [N] markers reliably (it has no per-chunk index handle to cite).
        # Vertex DOES return `grounding_supports` — claim segments paired
        # with the chunk indices that supported them — so we can insert
        # [N] markers automatically using those segment boundaries.
        # Important: do this BEFORE appending chunks to parent state so
        # we know the correct GLOBAL idx offset (per-call chunks become
        # entries [offset+1 .. offset+len(chunks)] in the eventual
        # citation list).
        if self.propagate_grounding_metadata and last_grounding_metadata:
            existing_count = len(
                tool_context.state.get("agent_grounding_chunks", []) or []
            )
            merged_text = self._annotate_with_grounding_supports(
                merged_text, last_grounding_metadata, idx_offset=existing_count
            )

        output_schema = _get_output_schema(self.agent)
        if output_schema:
            tool_result = validate_schema(output_schema, merged_text)
        else:
            tool_result = merged_text

        if self.propagate_grounding_metadata and last_grounding_metadata:
            # Upstream contract — kept so any caller that still reads
            # this exact key sees the metadata from the LAST sub-call.
            tool_context.state["temp:_adk_grounding_metadata"] = (
                last_grounding_metadata
            )
            # Per-call APPEND for our chat use-case: a multi-question fan-out
            # produces multiple rag_specialist invocations within one turn
            # and we need every call's chunks to survive into the final
            # citation list. We serialise to plain dicts here so the state
            # stays JSON-friendly when ADK persists it.
            self._append_grounding_chunks(tool_context, last_grounding_metadata)

        return tool_result

    @staticmethod
    def _annotate_with_grounding_supports(
        text: str, gm, *, idx_offset: int
    ) -> str:
        """Insert `[N]` markers into `text` at each grounding-support segment.

        `grounding_supports` is a list of {segment, grounding_chunk_indices}
        entries that Vertex returns alongside the answer. Each support says
        "this segment of the answer was grounded on these chunk indices".
        We append `[N1][N2]...` markers right after each segment, where
        `Nk = grounding_chunk_indices[k] + idx_offset + 1` (global 1-based
        idx; idx_offset is the count of chunks already accumulated by
        previous rag_specialist calls in the same turn).

        Insertions walk supports in DESCENDING end_index order so each
        write doesn't shift the offsets of later writes. start/end_index
        are byte offsets per the Vertex spec, so we encode/decode UTF-8
        around each splice.
        """
        supports = getattr(gm, "grounding_supports", None) or []
        if not supports:
            return text

        # Build (insert_at_byte, marker_str) pairs we'll splice in.
        edits: list[tuple[int, str]] = []
        for sup in supports:
            seg = getattr(sup, "segment", None)
            if seg is None:
                continue
            # Only annotate single-part text responses (part_index 0 / None).
            part_index = getattr(seg, "part_index", None)
            if part_index not in (None, 0):
                continue
            end_index = getattr(seg, "end_index", None)
            if end_index is None:
                continue
            chunk_indices = getattr(sup, "grounding_chunk_indices", None) or []
            if not chunk_indices:
                continue
            markers = "".join(f"[{i + idx_offset + 1}]" for i in chunk_indices)
            if not markers:
                continue
            edits.append((end_index, markers))

        if not edits:
            return text

        # Walk descending so earlier byte offsets stay valid.
        edits.sort(key=lambda x: x[0], reverse=True)
        buf = text.encode("utf-8")
        for at, markers in edits:
            at = max(0, min(at, len(buf)))
            buf = buf[:at] + markers.encode("utf-8") + buf[at:]
        return buf.decode("utf-8", errors="replace")

    @staticmethod
    def _capture_activity(event, tool_context: ToolContext) -> None:
        """Append thought / tool_call / tool_response entries from a sub-event
        into the parent's `state["agent_trace"]` list.

        Entry shape:
            {"agent": <author>, "kind": "model_thought" | "tool_call"
                                        | "tool_response",
             "seq": <int>, ...kind-specific fields}

        State writes here surface to the *parent* runner via the tool_response
        event's `actions.state_delta` (AgentTool's outer loop already merges
        sub-event state deltas into tool_context.state). Visibility-after-
        tool-completes matches what the activity panel needs.
        """
        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None) if content else None
        if not parts:
            return

        author = getattr(event, "author", None) or "unknown"
        existing = list(tool_context.state.get("agent_trace", []) or [])
        seq = len(existing)

        for p in parts:
            text = getattr(p, "text", None)
            is_thought = getattr(p, "thought", False)
            fc = getattr(p, "function_call", None)
            fr = getattr(p, "function_response", None)
            if text and is_thought:
                existing.append({
                    "agent": author,
                    "kind": "model_thought",
                    "seq": seq,
                    "text": text,
                })
                seq += 1
            elif fc is not None:
                existing.append({
                    "agent": author,
                    "kind": "tool_call",
                    "seq": seq,
                    "name": getattr(fc, "name", None),
                    "args": _safe_json(getattr(fc, "args", None) or {}),
                })
                seq += 1
            elif fr is not None:
                response_body = getattr(fr, "response", None) or {}
                if isinstance(response_body, dict):
                    response_dict = response_body
                else:
                    try:
                        response_dict = dict(response_body)
                    except Exception:
                        response_dict = {"_repr": str(response_body)}
                existing.append({
                    "agent": author,
                    "kind": "tool_response",
                    "seq": seq,
                    "name": getattr(fr, "name", None),
                    "response": response_dict,
                })
                seq += 1

        if len(existing) != len(tool_context.state.get("agent_trace", []) or []):
            tool_context.state["agent_trace"] = existing

    @staticmethod
    def _append_grounding_chunks(tool_context: ToolContext, gm) -> None:
        """Serialise this sub-call's grounding chunks into parent state.

        Each chunk lands as one entry under `state["agent_grounding_chunks"]`
        with the shape chats.py expects:
            {"agent": <sub-agent name>, "text", "title", "uri", "rag_chunk"}
        Multi-question fan-outs produce one StreamingAgentTool call per
        sub-question, so we APPEND rather than overwrite.
        """
        chunks = getattr(gm, "grounding_chunks", None) or []
        if not chunks:
            return
        author = "rag_specialist"  # only this agent has grounding wired today
        existing = list(tool_context.state.get("agent_grounding_chunks", []) or [])
        for c in chunks:
            rc = getattr(c, "retrieved_context", None)
            if rc is None:
                continue
            entry = {
                "agent": author,
                "text": getattr(rc, "text", None) or "",
                "title": getattr(rc, "title", None) or "",
                "uri": getattr(rc, "uri", None) or "",
            }
            rag_chunk = getattr(rc, "rag_chunk", None)
            if rag_chunk is not None:
                entry["rag_chunk_text"] = getattr(rag_chunk, "text", None) or ""
                page_span = getattr(rag_chunk, "page_span", None)
                if page_span is not None:
                    entry["page_first"] = getattr(page_span, "first_page", None)
                    entry["page_last"] = getattr(page_span, "last_page", None)
            existing.append(entry)
        tool_context.state["agent_grounding_chunks"] = existing


def _safe_json(obj) -> str:
    """Best-effort JSON serialisation; falls back to repr for non-JSON values."""
    import json
    try:
        return json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(obj)
