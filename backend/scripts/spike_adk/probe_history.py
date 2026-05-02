"""Probe 5 — seed prior history into a fresh InMemorySessionService session.

We need to confirm the public-API pattern for replaying Supabase
chat_messages into a fresh ADK session, then running async_stream_query
against that session_id and having Gemini receive the seeded history.

Approach:
  1. app.async_create_session(user_id=...) → session dict with id
  2. Reach into the InMemorySessionService backing the AdkApp's runner and
     call append_event() with hand-crafted user / model events.
  3. Run app.async_stream_query(message=..., session_id=session.id) and
     check the model's reply references the seeded fact.

If reaching into _tmpl_attrs["session_service"] is the only path, we
document it as the pattern. If a public hook exists (e.g. session_service
exposed via app.session_service), we prefer that.
"""
from __future__ import annotations

import asyncio
import json

from _common import USER_ID  # noqa: F401

from google.adk.agents.llm_agent import LlmAgent
from google.adk.events import Event
from google.genai import types
from vertexai.preview.reasoning_engines import AdkApp


async def main():
    agent = LlmAgent(
        name="probe5_agent",
        model="gemini-2.5-flash",
        description="Probe 5 history agent",
        instruction=(
            "Beantworte die Frage des Nutzers basierend AUSSCHLIESSLICH auf "
            "dem bisherigen Gespraech."
        ),
    )
    app = AdkApp(agent=agent)

    # Force runner setup so session_service exists.
    app.set_up()

    print("AdkApp _tmpl_attrs keys:", list(app._tmpl_attrs.keys()))
    sess_service = app._tmpl_attrs.get("session_service")
    print("session_service:", type(sess_service).__name__)

    sess = await app.async_create_session(user_id=USER_ID)
    sess_id = sess["id"] if isinstance(sess, dict) else sess.id
    print("created session_id:", sess_id)

    app_name = app._tmpl_attrs.get("app_name")
    print("app_name:", app_name)

    # Pull the live session object so we can append_event to it.
    live_sess = await sess_service.get_session(
        app_name=app_name, user_id=USER_ID, session_id=sess_id
    )
    print("live session events before seed:", len(live_sess.events))

    # Seed history.
    seed = [
        Event(
            author="user",
            content=types.Content(
                role="user",
                parts=[types.Part.from_text(text="Mein Lieblingstier ist der Steinbock.")],
            ),
        ),
        Event(
            author="probe5_agent",
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text="Verstanden — der Steinbock.")],
            ),
        ),
    ]
    for evt in seed:
        await sess_service.append_event(live_sess, evt)
    print("after seed, events:", len(live_sess.events))

    # Run a follow-up that depends on the seeded fact.
    print("\n=== Follow-up turn ===")
    final_text = []
    async for ev in app.async_stream_query(
        message="Was war mein Lieblingstier?",
        user_id=USER_ID,
        session_id=sess_id,
    ):
        # Collect text deltas.
        content = ev.get("content") or {}
        for part in content.get("parts") or []:
            txt = part.get("text") or ""
            if txt:
                final_text.append(txt)
    print("model answer accumulated:", "".join(final_text)[:500])
    answer = "".join(final_text).lower()
    if "steinbock" in answer:
        print("[probe5] PASS — seeded history was visible to the model")
    else:
        print("[probe5] FAIL — model did not reference seeded fact")


if __name__ == "__main__":
    asyncio.run(main())
