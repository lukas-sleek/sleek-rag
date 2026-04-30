"""Probe 6 — `app_name` stability across factory rebuilds.

Source read in T0 confirms:
- AdkApp __init__ does NOT accept `app_name` — it is hard-coded to
  `_DEFAULT_APP_NAME` ("default-app-name") on every instance.
- This is fine for the per-corpus factory: every cached AdkApp shares
  the same app_name automatically.

This probe verifies: build AdkApp_v1, create session, append events. Build
AdkApp_v2 (different agent tree) sharing the same app_name. Open the same
session_id via the second app. Are the seeded events visible?

NOTE: each AdkApp builds its own runner with its own InMemorySessionService.
So sessions WILL NOT survive across instances — they live in the per-app
service. The plan's "stable app_name → sessions survive eviction" claim
only holds for a backing service that's external to the app (e.g.
VertexAiSessionService against Agent Engine, or a shared
DatabaseSessionService).

This probe documents the gap so the plan's strategy (c) (Supabase replay
each turn) makes sense as the actual mechanism — app_name is irrelevant
for in-memory sessions.
"""
from __future__ import annotations

import asyncio

from _common import USER_ID  # noqa: F401

from google.adk.agents.llm_agent import LlmAgent
from google.adk.events import Event
from google.genai import types
from vertexai.preview.reasoning_engines import AdkApp


def make_app() -> AdkApp:
    return AdkApp(
        agent=LlmAgent(
            name=f"probe6_agent",
            model="gemini-2.5-flash",
            description="Probe 6 agent",
        )
    )


async def main():
    app1 = make_app()
    app1.set_up()
    app2 = make_app()
    app2.set_up()

    print("app1 app_name:", app1._tmpl_attrs.get("app_name"))
    print("app2 app_name:", app2._tmpl_attrs.get("app_name"))
    print("same session_service object?:", app1._tmpl_attrs.get("session_service") is app2._tmpl_attrs.get("session_service"))

    sess1 = await app1.async_create_session(user_id=USER_ID)
    sid = sess1["id"] if isinstance(sess1, dict) else sess1.id
    live = await app1._tmpl_attrs["session_service"].get_session(
        app_name=app1._tmpl_attrs["app_name"], user_id=USER_ID, session_id=sid
    )
    await app1._tmpl_attrs["session_service"].append_event(
        live,
        Event(
            author="user",
            content=types.Content(
                role="user", parts=[types.Part.from_text(text="hello from app1")]
            ),
        ),
    )

    # Try to read same session_id from app2.
    try:
        sess_in_app2 = await app2._tmpl_attrs["session_service"].get_session(
            app_name=app2._tmpl_attrs["app_name"], user_id=USER_ID, session_id=sid
        )
        print(f"[probe6] sess found in app2 with {len(sess_in_app2.events) if sess_in_app2 else 0} events")
    except Exception as exc:
        print(f"[probe6] sess NOT found in app2: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
