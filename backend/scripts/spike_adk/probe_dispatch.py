"""Probe 4 — multi-call dispatch: parallel or serialized?

Two FunctionTools, each sleeps 1s, then prompt asking for both. Time the
total round-trip vs a sequential baseline. If parallel: total ≈ ~1s plus
overhead. If serialized: ~2s plus overhead.
"""
from __future__ import annotations

import asyncio
import time

from _common import USER_ID  # noqa: F401

from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools import FunctionTool
from vertexai.preview.reasoning_engines import AdkApp


tool_starts: list[tuple[str, float]] = []
tool_ends: list[tuple[str, float]] = []


async def tell_joke() -> dict:
    """Returns a short German programming joke."""
    tool_starts.append(("joke", time.monotonic()))
    await asyncio.sleep(1.0)
    tool_ends.append(("joke", time.monotonic()))
    return {"joke": "Warum sortieren Programmierer ihre Liste? Damit sie der Reihe nach lacht."}


async def roll_dice() -> dict:
    """Rolls a six-sided die and returns the result."""
    tool_starts.append(("dice", time.monotonic()))
    await asyncio.sleep(1.0)
    tool_ends.append(("dice", time.monotonic()))
    return {"value": 4}


async def main():
    agent = LlmAgent(
        name="probe4_agent",
        model="gemini-2.5-flash",
        description="Probe 4 dispatch agent",
        instruction=(
            "Wenn der Nutzer mehrere Dinge auf einmal verlangt, rufe alle "
            "passenden Tools im selben Turn auf (parallel)."
        ),
        tools=[FunctionTool(func=tell_joke), FunctionTool(func=roll_dice)],
    )
    app = AdkApp(agent=agent)

    t0 = time.monotonic()
    async for _ in app.async_stream_query(
        message="Erzaehl mir einen Witz UND wuerfle gleichzeitig.",
        user_id=USER_ID,
    ):
        pass
    total = time.monotonic() - t0

    print(f"[probe4] total stream time: {total:.2f}s")
    print(f"[probe4] tool_starts: {tool_starts}")
    print(f"[probe4] tool_ends: {tool_ends}")
    if len(tool_starts) >= 2 and len(tool_ends) >= 2:
        s1, s2 = tool_starts[0][1], tool_starts[1][1]
        e1 = tool_ends[0][1]
        overlap = e1 - s2  # >0 means second tool started before first ended
        print(f"[probe4] start gap: {abs(s2 - s1):.3f}s, overlap: {overlap:.3f}s")
        print(
            f"[probe4] verdict: "
            f"{'PARALLEL' if overlap > 0.3 else 'SERIALIZED'}"
        )


if __name__ == "__main__":
    asyncio.run(main())
