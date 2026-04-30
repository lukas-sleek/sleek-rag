"""Probe 1 — event topology of `AdkApp.async_stream_query`.

Confirms:
- shape of yielded items (dict vs Event object — source says dict)
- where text deltas live, where tool calls live, where final-response signal lives
- presence and contents of `author` field

Pinned by the source read in T0:
- async_stream_query yields the result of `_utils.dump_event_for_json(event)`
  → a dict per event.
- Underlying Event object: `google.adk.events.Event` (fields: id, author,
  invocation_id, content (genai Content), partial bool, turn_complete bool,
  actions (state_delta, ...), function_calls property, function_responses
  property).

This script runs an AdkApp with one LlmAgent + one trivial FunctionTool and
prints every yielded event dict.
"""
from __future__ import annotations

import asyncio
import json

from _common import USER_ID  # noqa: F401, side-effect bootstrap

from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools import FunctionTool
from vertexai.preview.reasoning_engines import AdkApp


def get_weather(city: str) -> dict:
    """Returns the current weather for a city. Use for any weather question."""
    return {"city": city, "temp_c": 14, "summary": "klar"}


async def main():
    agent = LlmAgent(
        name="probe_agent",
        model="gemini-2.5-flash",
        description="Test agent",
        instruction="Beantworte kurz auf Deutsch. Nutze get_weather wenn nach Wetter gefragt.",
        tools=[FunctionTool(func=get_weather)],
    )
    app = AdkApp(agent=agent)

    print("=== Run 1: pure text answer ===")
    n = 0
    async for ev in app.async_stream_query(
        message="Sag kurz hallo.", user_id=USER_ID
    ):
        n += 1
        print(f"--- event {n} ---")
        print(json.dumps(ev, indent=2, default=str)[:1500])
        if n > 30:
            break

    print()
    print("=== Run 2: triggers tool call ===")
    n = 0
    async for ev in app.async_stream_query(
        message="Wie ist das Wetter in Bern?", user_id=USER_ID
    ):
        n += 1
        print(f"--- event {n} ---")
        print(json.dumps(ev, indent=2, default=str)[:1500])
        if n > 30:
            break


if __name__ == "__main__":
    asyncio.run(main())
