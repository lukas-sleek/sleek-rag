"""Prompt assembly for the chat endpoint.

Builds OpenAI-format messages from history + retrieved chunks. Optionally
attaches up to MAX_IMAGES_PER_TURN figure images as inline base64 image_url
parts. Image bytes are pulled from Supabase Storage on demand.
"""
from __future__ import annotations

import base64

from app.db import supabase
from app.retrieval import RetrievedChunk

SYSTEM_PROMPT = (
    "You are a technical RAG assistant. Answer based ONLY on the provided "
    "context chunks. Cite sources by the chunk number in square brackets, "
    "e.g. [1] or [2]. If the answer is not in the context, say so. When "
    "images are attached, refer to them as 'the figure' or by their label "
    "and explain what they show."
)

MAX_IMAGES_PER_TURN = 3
MAX_IMAGE_BYTES = 4 * 1024 * 1024  # Gemini hard cap per inline image.


def build_messages(
    *,
    query: str,
    history: list[dict],
    chunks: list[RetrievedChunk],
    system_prompt: str | None = None,
) -> list[dict]:
    """Assemble OpenAI-format messages with optional inline image parts."""
    context_block = _format_context(chunks)
    image_parts = _maybe_image_parts(chunks)

    user_text = f"{context_block}\n\n---\n\nQuestion: {query}"
    user_content: list[dict] | str
    if image_parts:
        user_content = [
            {"type": "text", "text": user_text},
            *image_parts,
        ]
    else:
        user_content = user_text

    return [
        {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": user_content},
    ]


def _format_context(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return "(No context retrieved.)"
    lines = ["Context:"]
    for i, c in enumerate(chunks, 1):
        head = f"[{i}] {c.filename} p.{c.page_start}"
        if c.figure_label:
            head += f" — {c.figure_label}"
        lines.append(f"{head}\n{c.content}")
    return "\n\n".join(lines)


def _maybe_image_parts(chunks: list[RetrievedChunk]) -> list[dict]:
    out: list[dict] = []
    for c in chunks:
        if not c.image_path:
            continue
        if len(out) >= MAX_IMAGES_PER_TURN:
            break
        try:
            data = supabase().storage.from_("chunk-images").download(c.image_path)
        except Exception:
            continue
        if not data or len(data) > MAX_IMAGE_BYTES:
            continue
        mime = "image/png"
        if c.image_path.lower().endswith((".jpg", ".jpeg")):
            mime = "image/jpeg"
        b64 = base64.b64encode(data).decode("ascii")
        out.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            }
        )
    return out
