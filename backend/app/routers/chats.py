import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth import current_user_id
from app.db import supabase
from app.openai_client import openai_client

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


class MessageOut(BaseModel):
    role: str
    content: str


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


@router.post("/{chat_id}/messages")
async def send_message(
    chat_id: str, body: MessageIn, user_id: str = Depends(current_user_id)
):
    chat = _load_chat(chat_id, user_id)

    if not chat["openai_thread_id"]:
        conv = openai_client().conversations.create()
        supabase().table("chats").update({"openai_thread_id": conv.id}).eq(
            "id", chat_id
        ).execute()
        chat["openai_thread_id"] = conv.id

    project = (
        supabase()
        .table("projects")
        .select("openai_vector_store_id")
        .eq("id", chat["project_id"])
        .single()
        .execute()
        .data
    )
    tools = []
    if project and project.get("openai_vector_store_id"):
        tools.append(
            {
                "type": "file_search",
                "vector_store_ids": [project["openai_vector_store_id"]],
            }
        )

    def event_stream():
        kwargs = dict(
            model="gpt-4o-mini",
            input=[{"role": "user", "content": body.text}],
            conversation=chat["openai_thread_id"],
            stream=True,
        )
        if tools:
            kwargs["tools"] = tools
        # Use `responses.create(stream=True, ...)` (not `responses.stream(...)`)
        # because the langsmith wrap_openai patch only instruments `.create`.
        # The streamed iterator must be fully consumed for langsmith to record
        # the run.
        stream = openai_client().responses.create(**kwargs)
        try:
            for event in stream:
                if event.type == "response.output_text.delta":
                    yield f"data: {json.dumps({'delta': event.delta})}\n\n"
        finally:
            stream.close()
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
