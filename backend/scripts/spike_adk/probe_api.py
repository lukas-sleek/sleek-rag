"""Probe 7 — confirm public AdkApp API surface vs. private attribute use.

Pre-answered from source read in T0:
  - AdkApp.async_stream_query(message=, user_id=, session_id=, run_config=)
    is the public streaming API.
  - AdkApp does NOT accept session_events= or events= kwargs.
  - AdkApp does NOT accept app_name= in __init__; it's hard-coded to
    _DEFAULT_APP_NAME ("default-app-name").
  - Public session methods on AdkApp:
      async_create_session(user_id=, session_id=None, state=None)
      async_get_session(user_id=, session_id=)
      async_list_sessions(user_id=)
      async_delete_session(user_id=, session_id=)
  - The session_service backing the runner lives at
      app._tmpl_attrs["session_service"]
    and exposes append_event(session, event) for history seeding. This is
    the private path the plan's T8c will use; document the dependency.

This probe just prints the dir() of AdkApp + the runner attributes to make
sure we haven't missed a public hook (e.g. seeded-events kwarg).
"""
from __future__ import annotations

import asyncio
import inspect

from _common import USER_ID  # noqa: F401

from google.adk.agents.llm_agent import LlmAgent
from vertexai.preview.reasoning_engines import AdkApp


async def main():
    app = AdkApp(
        agent=LlmAgent(
            name="probe7_agent", model="gemini-2.5-flash", description="x"
        )
    )

    print("=== AdkApp public methods (no underscore) ===")
    for name in sorted(dir(app)):
        if name.startswith("_"):
            continue
        attr = getattr(app, name)
        if callable(attr):
            try:
                sig = inspect.signature(attr)
            except (TypeError, ValueError):
                sig = "(?)"
            print(f"  {name}{sig}")

    app.set_up()
    print("\n=== _tmpl_attrs keys ===")
    for k, v in app._tmpl_attrs.items():
        print(f"  {k}: {type(v).__name__}")

    runner = app._tmpl_attrs.get("runner")
    if runner is not None:
        print("\n=== Runner class:", type(runner).__name__)
        print("=== Runner public attrs ===")
        for name in sorted(dir(runner)):
            if name.startswith("_"):
                continue
            attr = getattr(runner, name)
            if not callable(attr):
                print(f"  {name}: {type(attr).__name__}")

    sess_service = app._tmpl_attrs.get("session_service")
    if sess_service is not None:
        print("\n=== SessionService class:", type(sess_service).__name__)
        print("=== SessionService methods ===")
        for name in sorted(dir(sess_service)):
            if name.startswith("_"):
                continue
            attr = getattr(sess_service, name)
            if callable(attr):
                try:
                    sig = inspect.signature(attr)
                except (TypeError, ValueError):
                    sig = "(?)"
                print(f"  {name}{sig}")


if __name__ == "__main__":
    asyncio.run(main())
