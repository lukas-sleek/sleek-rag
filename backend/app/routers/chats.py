import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langsmith import traceable
from pydantic import BaseModel

from app.auth import current_user_id
from app.db import supabase
from app.openai_client import conversation_create, openai_client
from app.projektanalyse import (
    PROJEKTANALYSE_INSTRUCTIONS,
    PROJEKTANALYSE_TOOL,
    stream_projektanalyse,
)

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
    chat = _load_chat(chat_id, user_id)
    if not chat["openai_thread_id"]:
        return []
    items = openai_client().conversations.items.list(
        conversation_id=chat["openai_thread_id"]
    )
    out: list[MessageOut] = []
    # OpenAI returns most-recent-first; reverse to chronological for display.
    for item in reversed(list(items)):
        if getattr(item, "type", None) != "message":
            continue
        role = getattr(item, "role", None)
        content_parts = []
        for piece in getattr(item, "content", []) or []:
            text = getattr(piece, "text", None)
            if isinstance(text, str):
                content_parts.append(text)
            elif text is not None:
                content_parts.append(getattr(text, "value", "") or "")
        if role and content_parts:
            out.append(MessageOut(role=role, content="".join(content_parts)))
    return out


def _detect_projektanalyse_call(item) -> bool:
    return (
        getattr(item, "type", None) == "function_call"
        and getattr(item, "name", None) == "run_projektanalyse"
    )


@traceable(run_type="chain", name="chats.send_message")
async def _send_message_stream(
    *,
    chat: dict,
    text: str,
    chat_id: str,
    user_id: str,
    template: list[str] | None,
):
    """Top-level run for a chat turn. Setup ops (conversation_create, project
    lookup) and the responses.create LLM call all become children of this run.

    If the model calls the run_projektanalyse tool we close the live stream
    and hand off to the parallel batch handler in app.projektanalyse, which
    emits its own progress + final-report SSE events."""
    if not chat["openai_thread_id"]:
        conv_id = await asyncio.to_thread(conversation_create)
        await asyncio.to_thread(
            lambda: supabase()
            .table("chats")
            .update({"openai_thread_id": conv_id})
            .eq("id", chat_id)
            .execute()
        )
        chat["openai_thread_id"] = conv_id

    project = await asyncio.to_thread(
        lambda: supabase()
        .table("projects")
        .select("openai_vector_store_id")
        .eq("id", chat["project_id"])
        .single()
        .execute()
        .data
    )
    vector_store_id = (project or {}).get("openai_vector_store_id")

    tools: list[dict] = [PROJEKTANALYSE_TOOL]
    if vector_store_id:
        tools.append(
            {
                "type": "file_search",
                "vector_store_ids": [vector_store_id],
            }
        )

    kwargs = dict(
        model="gpt-4o-mini",
        input=[{"role": "user", "content": text}],
        conversation=chat["openai_thread_id"],
        stream=True,
        tools=tools,
        instructions=PROJEKTANALYSE_INSTRUCTIONS,
    )
    if vector_store_id:
        # Surface chunk content + scores in the response payload so they
        # appear in LangSmith traces. Without this only the file_search_call
        # queries/status are returned.
        kwargs["include"] = ["file_search_call.results"]

    stream = await asyncio.to_thread(openai_client().responses.create, **kwargs)
    projektanalyse_triggered = False
    try:
        for event in stream:
            etype = getattr(event, "type", None)
            if etype == "response.output_text.delta":
                yield f"data: {json.dumps({'delta': event.delta})}\n\n"
            elif etype == "response.output_item.done" and _detect_projektanalyse_call(
                getattr(event, "item", None)
            ):
                projektanalyse_triggered = True
                break
    finally:
        await asyncio.to_thread(stream.close)

    if projektanalyse_triggered:
        async for sse in stream_projektanalyse(
            template=template,
            vector_store_id=vector_store_id,
            conversation_id=chat["openai_thread_id"],
        ):
            yield sse
        return

    yield "data: [DONE]\n\n"


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
        stream = openai_client().responses.create(
            model="gpt-4o-mini",
            instructions=instructions,
            input=first_message,
            stream=True,
        )
    except Exception as exc:
        yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        yield "data: [DONE]\n\n"
        return
    try:
        for event in stream:
            if event.type == "response.output_text.delta":
                parts.append(event.delta)
                yield f"data: {json.dumps({'delta': event.delta})}\n\n"
    finally:
        stream.close()

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
