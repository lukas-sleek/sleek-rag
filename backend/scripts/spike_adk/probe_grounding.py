"""Probe whether managed Vertex RAG grounding can be wired into our pipeline
WITHOUT a tree rewrite.

Two probes, run back-to-back against the same corpus:

  A. Bare google.genai client + Tool(retrieval=Retrieval(vertex_rag_store=...))
     — matches the snippet that the user pasted from Vertex AI Studio.
     Confirms we get the same `Konfidenz` (= grounding_supports.confidence_scores)
     when we run it ourselves with our service account, not just in the
     Vertex Studio UI.

  B. Minimal ADK setup: ONE LlmAgent (Gemini 2.5 Flash) whose only tool is
     google.adk.tools.retrieval.VertexAiRagRetrieval. Wrap with AdkApp and
     stream events. Dumps every event that carries grounding_metadata so
     we can confirm the confidence + chunk metadata is reachable from
     `async_stream_query` — that's the only structural unknown blocking
     the swap in retrieval_tool.py.

Run: ./venv/bin/python -m scripts.spike_adk.probe_grounding

Read-only — no DB writes, no chat persistence. Throwaway probe.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from . import _common  # noqa: F401  — bootstraps env + vertexai.init

CORPUS_NAME = _common.CORPUS_NAME
USER_ID = _common.USER_ID
QUERY = "In welcher Phase werden Ingenieurdienstleistungen angefragt?"


def _dump(label: str, value: Any) -> None:
    print(f"\n--- {label} ---")
    try:
        print(json.dumps(value, indent=2, ensure_ascii=False, default=str))
    except Exception:
        print(repr(value))


def probe_a_bare_genai() -> None:
    """Replicates the user's Vertex AI Studio snippet, prints grounding_metadata."""
    from google import genai
    from google.genai import types

    print("\n" + "=" * 72)
    print("PROBE A: bare google.genai + Tool(retrieval=...)")
    print("=" * 72)

    client = genai.Client(
        vertexai=True,
        project=os.environ["GCP_PROJECT_ID"],
        location=os.environ.get("GCP_LOCATION", "europe-west3"),
    )

    config = types.GenerateContentConfig(
        temperature=1.0,
        top_p=1.0,
        max_output_tokens=8192,
        tools=[
            types.Tool(
                retrieval=types.Retrieval(
                    vertex_rag_store=types.VertexRagStore(
                        rag_resources=[
                            types.VertexRagStoreRagResource(rag_corpus=CORPUS_NAME)
                        ]
                    )
                )
            )
        ],
        thinking_config=types.ThinkingConfig(thinking_budget=-1),
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=QUERY,
        config=config,
    )

    print("\nFinal answer text:")
    print(response.text)

    cand = response.candidates[0] if response.candidates else None
    gm = getattr(cand, "grounding_metadata", None)
    if gm is None:
        print("\n[!] No grounding_metadata on the candidate.")
        return

    chunks = getattr(gm, "grounding_chunks", []) or []
    supports = getattr(gm, "grounding_supports", []) or []
    print(f"\ngrounding_chunks: {len(chunks)}")
    for i, ch in enumerate(chunks):
        rc = getattr(ch, "retrieved_context", None)
        if rc is None:
            print(f"  [{i}] (no retrieved_context)")
            continue
        title = getattr(rc, "title", None) or getattr(rc, "uri", None)
        text = (getattr(rc, "text", "") or "")[:120].replace("\n", " ")
        rag_chunk = getattr(rc, "rag_chunk", None)
        page_span = getattr(rag_chunk, "page_span", None) if rag_chunk else None
        print(f"  [{i}] {title}  page_span={page_span}  text={text!r}")

    print(f"\ngrounding_supports: {len(supports)}")
    for i, s in enumerate(supports):
        confs = list(getattr(s, "confidence_scores", []) or [])
        idxs = list(getattr(s, "grounding_chunk_indices", []) or [])
        seg = getattr(s, "segment", None)
        seg_text = (getattr(seg, "text", "") or "")[:80] if seg else ""
        print(
            f"  [{i}] confidence={confs}  chunks={idxs}  segment={seg_text!r}"
        )


async def probe_b_adk_managed_tool() -> None:
    """Wraps VertexAiRagRetrieval inside one LlmAgent, streams via AdkApp."""
    print("\n" + "=" * 72)
    print("PROBE B: ADK LlmAgent + VertexAiRagRetrieval (managed tool path)")
    print("=" * 72)

    from google.adk.agents.llm_agent import LlmAgent
    from google.adk.tools.retrieval.vertex_ai_rag_retrieval import (
        VertexAiRagRetrieval,
    )
    from vertexai import agent_engines
    from vertexai.preview import rag

    rag_tool = VertexAiRagRetrieval(
        name="retrieve_project_documents",
        description=(
            "Retrieves authoritative passages from the project's "
            "RAG corpus."
        ),
        rag_resources=[rag.RagResource(rag_corpus=CORPUS_NAME)],
        similarity_top_k=10,
    )
    agent = LlmAgent(
        name="rag_probe",
        model="gemini-2.5-flash",
        instruction=(
            "Beantworte die Nutzerfrage knapp anhand der RAG-Quellen. "
            "Sprache: Hochdeutsch."
        ),
        tools=[rag_tool],
    )

    app = agent_engines.AdkApp(agent=agent)
    session = await app.async_create_session(user_id=USER_ID)
    # AdkApp.async_create_session returns either a dict or an object
    # depending on version. Normalise to a session id.
    session_id = (
        session.get("id") if isinstance(session, dict) else session.id
    )

    seen_grounding_event = False
    async for event in app.async_stream_query(
        message=QUERY, session_id=session_id, user_id=USER_ID
    ):
        # Each event is a dict (JSON-serialised by AdkApp). Look for
        # grounding_metadata under content / candidates / etc.
        author = event.get("author")
        # Surface anything that smells like grounding metadata.
        gm = None
        # AdkApp serialises the candidate's grounding_metadata at the
        # event root in some shapes; check a few likely spots.
        for key in ("grounding_metadata", "groundingMetadata"):
            if key in event:
                gm = event[key]
                break
        if gm is None:
            actions = event.get("actions") or {}
            for key in ("grounding_metadata", "groundingMetadata"):
                if key in actions:
                    gm = actions[key]
                    break
        # Some versions tuck it under content.parts[i].
        if gm is None:
            content = event.get("content") or {}
            for p in content.get("parts") or []:
                for key in ("grounding_metadata", "groundingMetadata"):
                    if key in p:
                        gm = p[key]
                        break
                if gm:
                    break

        text = ""
        for p in (event.get("content") or {}).get("parts") or []:
            t = p.get("text")
            if t:
                text += t

        flag = "[GM]" if gm else "    "
        print(f"{flag} author={author!r:24} text={text[:80]!r}")
        if gm:
            seen_grounding_event = True
            _dump("event.grounding_metadata", gm)

    if not seen_grounding_event:
        print(
            "\n[!] No grounding_metadata seen on any streamed event — "
            "AdkApp may not be surfacing it. Need a callback to capture."
        )


def main() -> int:
    if not os.environ.get("GCP_PROJECT_ID"):
        print("ERROR: GCP_PROJECT_ID not set. Source .env first.", file=sys.stderr)
        return 2
    probe_a_bare_genai()
    asyncio.run(probe_b_adk_managed_tool())
    return 0


if __name__ == "__main__":
    sys.exit(main())
