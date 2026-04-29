"""Variant A: Vanilla Gemini + Vertex RAG retrieval tool.

Direct port of the user's JS test — same model, same config, same corpus.
This IS the post-migration target (18.3): gemini-2.5-flash, temperature=1.0,
top_p=0.95, max_output_tokens=65535, thinking_level=HIGH, all safety OFF,
Vertex RAG grounding tool with no rag_retrieval_config (defaults).
"""

import os
import time

from google import genai
from google.genai import types


def _env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None:
        raise RuntimeError(f"missing env var: {name}")
    return val


def make_client() -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=_env("GCP_PROJECT_ID"),
        location=os.environ.get("GCP_LOCATION", "europe-west3"),
    )


def make_chat(client: genai.Client):
    rag_corpus = _env("BENCHMARK_RAG_CORPUS")
    model = os.environ.get("BENCHMARK_VANILLA_MODEL", "gemini-2.5-flash")

    tools = [
        types.Tool(
            retrieval=types.Retrieval(
                vertex_rag_store=types.VertexRagStore(
                    rag_resources=[
                        types.VertexRagStoreRagResource(rag_corpus=rag_corpus)
                    ]
                )
            )
        )
    ]

    config = types.GenerateContentConfig(
        max_output_tokens=65535,
        temperature=1.0,
        top_p=0.95,
        tools=tools,
        thinking_config=types.ThinkingConfig(thinking_level="HIGH"),
        safety_settings=[
            types.SafetySetting(category=c, threshold="OFF")
            for c in [
                "HARM_CATEGORY_HATE_SPEECH",
                "HARM_CATEGORY_DANGEROUS_CONTENT",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "HARM_CATEGORY_HARASSMENT",
            ]
        ],
    )
    return client.chats.create(model=model, config=config)


def _extract_grounding_uris(response) -> list[str]:
    uris: list[str] = []
    try:
        meta = response.candidates[0].grounding_metadata
    except (AttributeError, IndexError):
        return uris
    if meta is None:
        return uris
    for chunk in (meta.grounding_chunks or []):
        retrieved = getattr(chunk, "retrieved_context", None)
        if retrieved and getattr(retrieved, "uri", None):
            uris.append(retrieved.uri)
    return uris


def ask(chat, question: str) -> dict:
    """Ask one question, return {answer, grounding_uris, latency_s}."""
    t0 = time.monotonic()
    response = chat.send_message(question)
    latency = time.monotonic() - t0

    text = ""
    try:
        text = response.text or ""
    except Exception:
        try:
            parts = response.candidates[0].content.parts
            text = "".join(getattr(p, "text", "") or "" for p in parts)
        except Exception:
            text = ""

    return {
        "answer": text,
        "grounding_uris": _extract_grounding_uris(response),
        "latency_s": round(latency, 2),
    }


def run_question_set(questions: list[dict]) -> list[dict]:
    client = make_client()
    chat = make_chat(client)
    results: list[dict] = []
    for q in questions:
        r = ask(chat, q["question"])
        results.append(
            {
                "id": q["id"],
                "question": q["question"],
                **r,
            }
        )
    return results
