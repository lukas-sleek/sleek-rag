"""Chat endpoints — Pattern A (plan 18.3).

A chat turn = one Vertex RAG-grounded Gemini chat session. The custom
multi-iteration tool loop, sufficiency rater, answer verifier, force-tool
guard, multi-tool dispatch, and ref_offset accounting are gone. Domain
rules live in app.system_instructions.SYSTEM_INSTRUCTION; retrieval is
delegated to Vertex via the grounding tool from app.vertex_rag_grounding.

The session still hands off to the projektanalyse v1/v2 streamers when
the model elects either function tool — both keep their existing SSE
shape so the frontend is unchanged.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from google import genai
from google.genai import types
from google.oauth2 import service_account
from langsmith import traceable
from pydantic import BaseModel

from app.auth import current_user_id
from app.citations import annotate_answer_with_refs, grounding_to_citations
from app.config import settings
from app.db import supabase
from app.gemini_client import gemini_client_untraced
from app.projektanalyse import (
    PROJEKTANALYSE_DECL,
    PROJEKTANALYSE_V2_DECL,
    stream_projektanalyse,
    stream_projektanalyse_v2,
)
from app.system_instructions import SYSTEM_INSTRUCTION
from app.vertex_rag_grounding import grounding_tool_for_project

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chats", tags=["chats"])


# Vertex AI's Gemini 2.5 endpoint rejects thinking_level (string enum).
# Only thinking_budget (token count) is accepted. HIGH on Flash maps to
# 24576 — same translation used by the 18.0.1 vanilla benchmark client
# (scripts/benchmark/clients/vanilla.py); 18.3 mirrors it verbatim.
_THINKING_BUDGET_HIGH = 24576
_HISTORY_LIMIT = 20


_genai_client: genai.Client | None = None


def _client() -> genai.Client:
    global _genai_client
    if _genai_client is not None:
        return _genai_client
    creds = None
    if settings.gcp_service_account_json_path:
        creds = service_account.Credentials.from_service_account_file(
            settings.gcp_service_account_json_path,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
    _genai_client = genai.Client(
        vertexai=True,
        project=settings.gcp_project_id,
        location=settings.gcp_location,
        credentials=creds,
    )
    return _genai_client


def _friendly_gemini_error(_exc: Exception) -> str:
    """Vendor-neutral German message shown to the user when the upstream LLM
    fails. The technical detail (provider, HTTP code, error class) is logged
    via `log.warning` at the call site — never surfaced in the transcript."""
    return (
        "_⚠️ Die Antwort konnte gerade nicht erzeugt werden. "
        "Bitte in ein paar Sekunden erneut versuchen._"
    )


class ChatIn(BaseModel):
    project_id: str
    title: str = "New chat"


class ChatPatch(BaseModel):
    title: str


class ChatOut(BaseModel):
    id: str
    project_id: str
    title: str


class MessageIn(BaseModel):
    text: str
    projektanalyse_template: list[str] | None = None


class TitleIn(BaseModel):
    first_message: str


class MessageOut(BaseModel):
    role: str
    content: str
    citations: list[dict] | None = None


def _load_chat(chat_id: str, user_id: str) -> dict:
    res = (
        supabase()
        .table("chats")
        .select("*")
        .eq("id", chat_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "chat not found")
    return res.data[0]


@router.get("", response_model=list[ChatOut])
def list_chats(project_id: str, user_id: str = Depends(current_user_id)):
    res = (
        supabase()
        .table("chats")
        .select("id,project_id,title")
        .eq("user_id", user_id)
        .eq("project_id", project_id)
        .order("created_at")
        .execute()
    )
    return res.data


@router.post("", response_model=ChatOut)
def create_chat(body: ChatIn, user_id: str = Depends(current_user_id)):
    res = (
        supabase()
        .table("chats")
        .insert({"user_id": user_id, "project_id": body.project_id, "title": body.title})
        .execute()
    )
    return res.data[0]


@router.patch("/{chat_id}", response_model=ChatOut)
def rename_chat(chat_id: str, body: ChatPatch, user_id: str = Depends(current_user_id)):
    res = (
        supabase()
        .table("chats")
        .update({"title": body.title})
        .eq("id", chat_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "not found")
    return res.data[0]


@router.delete("/{chat_id}")
def delete_chat(chat_id: str, user_id: str = Depends(current_user_id)):
    res = (
        supabase()
        .table("chats")
        .delete()
        .eq("id", chat_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "not found")
    return {"deleted": chat_id}


@router.get("/{chat_id}/messages", response_model=list[MessageOut])
def list_messages(chat_id: str, user_id: str = Depends(current_user_id)):
    _load_chat(chat_id, user_id)
    res = (
        supabase()
        .table("chat_messages")
        .select("role,content,citations")
        .eq("chat_id", chat_id)
        .eq("user_id", user_id)
        .order("created_at")
        .execute()
    )
    return [
        MessageOut(
            role=r["role"], content=r["content"], citations=r.get("citations")
        )
        for r in (res.data or [])
    ]


def _build_config(project_id: str) -> types.GenerateContentConfig:
    """Mirror the vanilla benchmark client config verbatim (18.3 acceptance
    criterion). Returns a fresh config per turn so the bound corpus reflects
    the project's current rag_corpus_name."""
    grounding = grounding_tool_for_project(project_id)
    function_tools = types.Tool(
        function_declarations=[PROJEKTANALYSE_DECL, PROJEKTANALYSE_V2_DECL]
    )
    tools: list[types.Tool] = [function_tools]
    if grounding is not None:
        tools.insert(0, grounding)

    return types.GenerateContentConfig(
        max_output_tokens=65535,
        temperature=1.0,
        top_p=0.95,
        thinking_config=types.ThinkingConfig(
            thinking_budget=_THINKING_BUDGET_HIGH
        ),
        safety_settings=[
            types.SafetySetting(category=c, threshold="OFF")
            for c in (
                "HARM_CATEGORY_HATE_SPEECH",
                "HARM_CATEGORY_DANGEROUS_CONTENT",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "HARM_CATEGORY_HARASSMENT",
            )
        ],
        system_instruction=SYSTEM_INSTRUCTION,
        tools=tools,
    )


def _load_history_sync(chat_id: str, user_id: str) -> list[types.Content]:
    """Last `_HISTORY_LIMIT` messages from this chat, newest-last, formatted
    as google-genai Content. The just-inserted user turn is excluded by
    upstream callers — it's passed as the new message to send_message_stream.
    Per CLAUDE.md ("Module 2+ uses stateless completions"), we re-load
    history per request rather than reusing a long-lived chat session."""
    rows = (
        supabase()
        .table("chat_messages")
        .select("id,role,content,created_at")
        .eq("chat_id", chat_id)
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(_HISTORY_LIMIT)
        .execute()
        .data
        or []
    )
    history: list[types.Content] = []
    for r in reversed(rows):
        role = r["role"]
        if role not in ("user", "assistant"):
            continue
        # genai uses "model" for assistant turns
        genai_role = "model" if role == "assistant" else "user"
        content = r["content"] or ""
        history.append(
            types.Content(
                role=genai_role, parts=[types.Part.from_text(text=content)]
            )
        )
    return history


def _extract_function_call(chunk) -> types.FunctionCall | None:
    """Pull the first function_call part out of a streamed chunk, if any.
    Vertex emits function calls as their own Part — they don't interleave
    with text in the same chunk."""
    candidates = getattr(chunk, "candidates", None) or []
    if not candidates:
        return None
    content = getattr(candidates[0], "content", None)
    if content is None:
        return None
    for part in getattr(content, "parts", None) or []:
        fc = getattr(part, "function_call", None)
        if fc is not None and getattr(fc, "name", None):
            return fc
    return None


def _chunk_text(chunk) -> str:
    """Extract joined text from a streamed chunk's text parts.

    Avoids `chunk.text` — that property emits warnings (and in some genai
    versions raises) when the chunk has no text part, e.g. function-call
    or terminal-finish chunks. We read the parts directly so a chunk
    without text deltas just returns ''."""
    candidates = getattr(chunk, "candidates", None) or []
    if not candidates:
        return ""
    content = getattr(candidates[0], "content", None)
    if content is None:
        return ""
    pieces: list[str] = []
    for part in getattr(content, "parts", None) or []:
        text = getattr(part, "text", None)
        if text:
            pieces.append(text)
    return "".join(pieces)


def _chunk_has_grounding(chunk) -> bool:
    """True iff the chunk's candidate carries grounding_chunks. Vertex emits
    grounding_metadata on whichever streamed chunk completes the retrieval —
    not necessarily the last chunk overall, so we track the latest grounded
    chunk separately for citation extraction (plan 18.3 Pattern A bug fix)."""
    candidates = getattr(chunk, "candidates", None) or []
    if not candidates:
        return False
    meta = getattr(candidates[0], "grounding_metadata", None)
    if meta is None:
        return False
    return bool(getattr(meta, "grounding_chunks", None))


@traceable(run_type="chain", name="chats.send_message")
async def _send_message_stream(
    *,
    chat: dict,
    text: str,
    chat_id: str,
    user_id: str,
    template: list[str] | None,
):
    """Pattern A streaming chat turn.

    1. Persist the user message.
    2. Build a chat session bound to the project's RAG corpus + the two
       projektanalyse function tools.
    3. Stream the model's response. Text deltas → SSE delta frames.
       Function calls (run_projektanalyse / run_projektanalyse_v2) hand
       the rest of the SSE stream off to the matching streamer.
    4. After the stream completes, regex-enrich grounding_metadata into
       citations, emit an SSE meta frame, persist the assistant message,
       emit done.
    """
    project_id = chat["project_id"]

    # 1. Persist user message.
    await asyncio.to_thread(
        lambda: supabase()
        .table("chat_messages")
        .insert(
            {
                "chat_id": chat_id,
                "user_id": user_id,
                "role": "user",
                "content": text,
            }
        )
        .execute()
    )

    # 2. Load history + build the chat session.
    try:
        history = await asyncio.to_thread(_load_history_sync, chat_id, user_id)
        config = await asyncio.to_thread(_build_config, project_id)
    except Exception as exc:  # noqa: BLE001 — coarse so we can always surface a banner
        log.exception("chat session build failed: %s", exc)
        yield f"data: {json.dumps({'type': 'delta', 'content': _friendly_gemini_error(exc)})}\n\n"
        yield f"data: {json.dumps({'type': 'meta', 'citations': []})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return

    # `_load_history_sync` includes the just-persisted user turn — drop it
    # so we can pass the message as send_message_stream's argument instead.
    if history and history[-1].role == "user":
        history = history[:-1]

    chat_session = _client().aio.chats.create(
        model=settings.gemini_chat_model,
        config=config,
        history=history,
    )

    answer_parts: list[str] = []
    grounded_chunk = None  # latest chunk whose candidate carried grounding_chunks
    handed_off = False

    try:
        stream = await chat_session.send_message_stream(text)
        async for chunk in stream:
            chunk_text = _chunk_text(chunk)
            if chunk_text:
                answer_parts.append(chunk_text)
                yield f"data: {json.dumps({'type': 'delta', 'content': chunk_text})}\n\n"

            fc = _extract_function_call(chunk)
            if fc is not None:
                handed_off = True
                if fc.name == "run_projektanalyse":
                    async for sse in stream_projektanalyse(
                        template=template, chat_id=chat_id, user_id=user_id
                    ):
                        yield sse
                    return
                if fc.name == "run_projektanalyse_v2":
                    async for sse in stream_projektanalyse_v2(
                        template=template, chat_id=chat_id, user_id=user_id
                    ):
                        yield sse
                    return
                # Unknown function call — log + ignore. Continue the stream
                # so the model has a chance to recover with a text answer.
                log.warning("unknown function_call from model: %s", fc.name)
                handed_off = False

            if _chunk_has_grounding(chunk):
                grounded_chunk = chunk
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "gemini chat stream failed: %s: %s",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        notice = _friendly_gemini_error(exc)
        if answer_parts:
            yield f"data: {json.dumps({'type': 'delta', 'content': chr(10) + chr(10) + notice})}\n\n"
        else:
            yield f"data: {json.dumps({'type': 'delta', 'content': notice})}\n\n"
        yield f"data: {json.dumps({'type': 'meta', 'citations': []})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return

    if handed_off:
        # Hand-off path already returned its own done frame.
        return

    # 4. Citations + inline `[N]` ref annotation + persist + done.
    citations = await grounding_to_citations(grounded_chunk, project_id)
    raw_answer = "".join(answer_parts)
    annotated = annotate_answer_with_refs(grounded_chunk, raw_answer)
    # Send the annotated text alongside citations so the frontend can replace
    # the streamed content with the inline-cited version once meta arrives.
    # `[N]` markers come from grounding_supports — Vertex's structural span-
    # to-chunk linkage; the existing chat.tsx `[\\d+]` regex picks them up.
    yield "data: " + json.dumps(
        {"type": "meta", "citations": citations, "content": annotated}
    ) + "\n\n"

    persisted_text = annotated.strip()
    if persisted_text:
        msg = await asyncio.to_thread(
            lambda: supabase()
            .table("chat_messages")
            .insert(
                {
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "role": "assistant",
                    "content": persisted_text,
                    "citations": citations,
                }
            )
            .execute()
            .data[0]
        )
        yield f"data: {json.dumps({'type': 'done', 'message_id': msg['id']})}\n\n"
    else:
        yield f"data: {json.dumps({'type': 'done'})}\n\n"


@router.post("/{chat_id}/messages")
async def send_message(
    chat_id: str, body: MessageIn, user_id: str = Depends(current_user_id)
):
    chat = _load_chat(chat_id, user_id)
    return StreamingResponse(
        _send_message_stream(
            chat=chat,
            text=body.text,
            chat_id=chat_id,
            user_id=user_id,
            template=body.projektanalyse_template,
        ),
        media_type="text/event-stream",
    )


def _title_stream(*, first_message: str, chat_id: str, user_id: str):
    # LangSmith tracing intentionally disabled for auto-title: low-value, high
    # volume, and adds noise that drowns out the chat traces. Uses the
    # unwrapped Gemini client so the call doesn't get captured even when
    # LANGSMITH_API_KEY is set. (Title still goes through the OpenAI-compat
    # endpoint — Pattern A migration didn't touch this path.)
    instructions = (
        "Du erhältst die erste Nachricht eines Chats. Erzeuge daraus einen "
        "prägnanten Titel mit 3 bis 6 Wörtern. Kein Punkt am Ende, keine "
        "Anführungszeichen. Antworte ausschließlich mit dem Titel."
    )
    parts: list[str] = []
    try:
        stream = gemini_client_untraced().chat.completions.create(
            model=settings.gemini_chat_model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": first_message},
            ],
            stream=True,
            extra_body={"reasoning_effort": "none"},
        )
    except Exception as exc:
        yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        yield "data: [DONE]\n\n"
        return
    try:
        for event in stream:
            if not event.choices:
                continue
            chunk = event.choices[0].delta
            piece = getattr(chunk, "content", None)
            if piece:
                parts.append(piece)
                yield f"data: {json.dumps({'delta': piece})}\n\n"
    except Exception as exc:
        # Title is best-effort: keep whatever we got, fall through to persist.
        log.warning("title stream interrupted: %s", exc)
    finally:
        try:
            stream.close()
        except Exception:
            pass

    title = "".join(parts).strip().strip('"').strip("'").strip()
    if title:
        (
            supabase()
            .table("chats")
            .update({"title": title})
            .eq("id", chat_id)
            .eq("user_id", user_id)
            .execute()
        )
    yield "data: [DONE]\n\n"


@router.post("/{chat_id}/title")
def auto_title(
    chat_id: str, body: TitleIn, user_id: str = Depends(current_user_id)
):
    _load_chat(chat_id, user_id)
    return StreamingResponse(
        _title_stream(
            first_message=body.first_message, chat_id=chat_id, user_id=user_id
        ),
        media_type="text/event-stream",
    )
