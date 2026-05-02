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
        output_schema = _get_output_schema(self.agent)
        if output_schema:
            tool_result = validate_schema(output_schema, merged_text)
        else:
            tool_result = merged_text

        if self.propagate_grounding_metadata and last_grounding_metadata:
            tool_context.state["temp:_adk_grounding_metadata"] = (
                last_grounding_metadata
            )

        return tool_result

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


def _safe_json(obj) -> str:
    """Best-effort JSON serialisation; falls back to repr for non-JSON values."""
    import json
    try:
        return json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(obj)
