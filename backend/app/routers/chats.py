import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langsmith import traceable
from pydantic import BaseModel

from app.auth import current_user_id
from app.config import settings
from app.db import supabase
from app.gemini_client import gemini_client
from app.projektanalyse import (
    PROJEKTANALYSE_INSTRUCTIONS,
    PROJEKTANALYSE_TOOL,
    PROJEKTANALYSE_V2_TOOL,
    stream_projektanalyse,
    stream_projektanalyse_v2,
)
from app.prompt import build_messages
from app.retrieval import retrieve

router = APIRouter(prefix="/api/chats", tags=["chats"])


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


@traceable(run_type="tool", name="db.load_chat")
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
@traceable(run_type="chain", name="chats.list")
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
@traceable(run_type="chain", name="chats.create")
def create_chat(body: ChatIn, user_id: str = Depends(current_user_id)):
    res = (
        supabase()
        .table("chats")
        .insert({"user_id": user_id, "project_id": body.project_id, "title": body.title})
        .execute()
    )
    return res.data[0]


@router.patch("/{chat_id}", response_model=ChatOut)
@traceable(run_type="chain", name="chats.rename")
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
@traceable(run_type="chain", name="chats.delete")
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
@traceable(run_type="chain", name="chats.list_messages")
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


def _detect_projektanalyse_call(tool_call) -> str | None:
    """Returns 'v1', 'v2', or None depending on which tool the model called."""
    fn = getattr(tool_call, "function", None)
    name = getattr(fn, "name", None) if fn else None
    if name == "run_projektanalyse":
        return "v1"
    if name == "run_projektanalyse_v2":
        return "v2"
    return None


@traceable(run_type="chain", name="chats.send_message")
async def _send_message_stream(
    *,
    chat: dict,
    text: str,
    chat_id: str,
    user_id: str,
    template: list[str] | None,
):
    """Top-level run for a chat turn.

    Persists the user message, retrieves chunks from document_chunks, calls
    Gemini with tools attached, streams deltas and a citations meta frame.
    Hands off to projektanalyse v1/v2 if the model triggers a tool."""

    # 1. Persist user message.
    user_msg = await asyncio.to_thread(
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
        .data[0]
    )

    # 2. Load history (last 20 messages, excluding the just-inserted user message).
    history_rows = await asyncio.to_thread(
        lambda: (
            supabase()
            .table("chat_messages")
            .select("role,content,created_at")
            .eq("chat_id", chat_id)
            .eq("user_id", user_id)
            .neq("id", user_msg["id"])
            .order("created_at", desc=True)
            .limit(20)
            .execute()
            .data
            or []
        )
    )
    history = [
        {"role": r["role"], "content": r["content"]}
        for r in reversed(history_rows)
        if r["role"] in ("user", "assistant")
    ]

    # 3. Retrieve chunks.
    project_id = chat["project_id"]
    chunks = await asyncio.to_thread(
        retrieve, query=text, project_id=project_id, user_id=user_id, top_k=8
    )
    citations = [c.to_citation() for c in chunks]

    # 4. Emit meta frame with citations up-front.
    yield f"data: {json.dumps({'type': 'meta', 'citations': citations})}\n\n"

    # 5. Build messages and call Gemini.
    messages = build_messages(
        query=text,
        history=history,
        chunks=chunks,
        system_prompt=PROJEKTANALYSE_INSTRUCTIONS,
    )
    tools: list[dict] = [PROJEKTANALYSE_TOOL, PROJEKTANALYSE_V2_TOOL]

    stream = await asyncio.to_thread(
        lambda: gemini_client().chat.completions.create(
            model=settings.gemini_chat_model,
            messages=messages,
            tools=tools,
            stream=True,
        )
    )

    parts: list[str] = []
    triggered: str | None = None
    pending_tool_calls: dict[int, dict] = {}

    try:
        for event in stream:
            if not event.choices:
                continue
            choice = event.choices[0]
            delta = choice.delta
            if getattr(delta, "content", None):
                parts.append(delta.content)
                yield f"data: {json.dumps({'type': 'delta', 'content': delta.content})}\n\n"
            tool_calls = getattr(delta, "tool_calls", None) or []
            for tc in tool_calls:
                idx = getattr(tc, "index", 0) or 0
                slot = pending_tool_calls.setdefault(idx, {"name": None})
                fn = getattr(tc, "function", None)
                if fn and getattr(fn, "name", None):
                    slot["name"] = fn.name
            if pending_tool_calls:
                for slot in pending_tool_calls.values():
                    name = slot.get("name")
                    if name == "run_projektanalyse":
                        triggered = "v1"
                        break
                    if name == "run_projektanalyse_v2":
                        triggered = "v2"
                        break
                if triggered:
                    break
    finally:
        try:
            await asyncio.to_thread(stream.close)
        except Exception:
            pass

    # 6. Hand off to projektanalyse if triggered (handles its own persistence
    #    + done frame). Otherwise persist assistant message and emit done.
    if triggered == "v1":
        async for sse in stream_projektanalyse(
            template=template, chat_id=chat_id, user_id=user_id
        ):
            yield sse
        return

    if triggered == "v2":
        async for sse in stream_projektanalyse_v2(
            template=template, chat_id=chat_id, user_id=user_id
        ):
            yield sse
        return

    assistant_text = "".join(parts).strip()
    if assistant_text:
        msg = await asyncio.to_thread(
            lambda: supabase()
            .table("chat_messages")
            .insert(
                {
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "role": "assistant",
                    "content": assistant_text,
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


@traceable(run_type="chain", name="chats.auto_title")
def _title_stream(*, first_message: str, chat_id: str, user_id: str):
    instructions = (
        "Du erhältst die erste Nachricht eines Chats. Erzeuge daraus einen "
        "prägnanten Titel mit 3 bis 6 Wörtern. Kein Punkt am Ende, keine "
        "Anführungszeichen. Antworte ausschließlich mit dem Titel."
    )
    parts: list[str] = []
    try:
        stream = gemini_client().chat.completions.create(
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
