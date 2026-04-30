"""Chat endpoints — ADK multi-agent edition (plan 19.0).

A chat turn = one orchestrator-driven AdkApp run. The orchestrator
(gemini-2.5-pro) routes to:
  - rag_specialist (per-question Flash worker) — Projektfragen
  - web_researcher (Flash + Google search + UrlContext) — externe Fragen
  - run_projektanalyse_v2 — explicit user-elected handoff to v2 streamer

Per-turn lifecycle:
  1. Persist user message to chat_messages.
  2. Resolve corpus -> get-or-build a per-corpus AdkApp (LRU-cached).
  3. Seed a fresh in-memory ADK session with replayed Supabase history.
  4. Stream events; forward orchestrator model_text deltas to SSE.
     If the orchestrator emits a run_projektanalyse_v2 tool_response,
     abort and resume from stream_projektanalyse_v2.
  5. Read state["citations"], dedupe + globally renumber, persist.

SSE shape unchanged from 18.3: delta -> meta -> done.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langsmith import traceable
from pydantic import BaseModel

from app.adk.app_factory import get_or_build_app
from app.adk.citation_aggregator import dedupe_and_renumber, rewrite_refs
from app.adk.event_translator import (
    event_author,
    event_kind,
    event_text,
    is_v2_handoff,
)
from app.adk.history import seed_session
from app.auth import current_user_id
from app.config import settings
from app.db import supabase
from app.gemini_client import gemini_client_untraced
from app.projektanalyse import stream_projektanalyse_v2

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chats", tags=["chats"])


def _friendly_gemini_error(_exc: Exception) -> str:
    """Vendor-neutral German message shown to the user when the upstream LLM
    fails. The technical detail (provider, HTTP code, error class) is logged
    via `log.warning` at the call site — never surfaced in the transcript."""
    return (
        "_⚠️ Die Antwort konnte gerade nicht erzeugt werden. "
        "Bitte in ein paar Sekunden erneut versuchen._"
    )


# Trace SSE emission is gated to debug accounts so production users don't see
# raw agent internals (and pay the bandwidth tax for events we'd otherwise
# discard). Set DEBUG_TRACE_USER_EMAILS to a comma-separated list to expand.
_DEBUG_TRACE_USER_EMAILS = {"test@test.com"}
_user_email_cache: dict[str, str | None] = {}


def _is_debug_user(user_id: str) -> bool:
    """Look up the user's email in Supabase Auth, cache the result, return
    True iff it's in the debug allow-list. Cache is process-local, never
    invalidated — email changes are rare and a stale negative just means
    no traces for one session."""
    cached = _user_email_cache.get(user_id)
    if cached is None and user_id not in _user_email_cache:
        try:
            res = supabase().auth.admin.get_user_by_id(user_id)
            email = getattr(res.user, "email", None) if res and res.user else None
        except Exception as exc:  # noqa: BLE001
            log.debug("debug-user lookup failed for %s: %s", user_id, exc)
            email = None
        _user_email_cache[user_id] = email
        cached = email
    return (cached or "").lower() in _DEBUG_TRACE_USER_EMAILS


_TRACE_TEXT_PREVIEW_LIMIT = 600
_TRACE_ARGS_PREVIEW_LIMIT = 400


def _build_trace_frame(event: dict, *, event_id: int) -> dict | None:
    """Reduce one ADK event dict into a compact trace frame for the UI.

    Shape:
      {type: "trace", id, author, kind,
       name?,        # tool name for tool_call / tool_response
       args?,        # truncated tool_call arguments (str)
       response?,    # truncated tool_response body (str)
       text?}        # truncated model text preview
    """
    kind = event_kind(event)
    if kind == "other":
        return None
    author = event_author(event) or "unknown"
    frame: dict = {
        "type": "trace",
        "id": f"evt-{event_id}",
        "author": author,
        "kind": kind,
    }
    parts = (event.get("content") or {}).get("parts") or []
    if kind == "tool_call":
        for p in parts:
            fc = p.get("function_call")
            if fc:
                frame["name"] = fc.get("name")
                frame["args"] = json.dumps(fc.get("args") or {}, ensure_ascii=False)[
                    :_TRACE_ARGS_PREVIEW_LIMIT
                ]
                break
    elif kind == "tool_response":
        for p in parts:
            fr = p.get("function_response")
            if fr:
                frame["name"] = fr.get("name")
                frame["response"] = json.dumps(
                    fr.get("response") or {}, ensure_ascii=False
                )[:_TRACE_ARGS_PREVIEW_LIMIT]
                break
    elif kind == "model_text":
        frame["text"] = event_text(event)[:_TRACE_TEXT_PREVIEW_LIMIT]
    return frame


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


def _persist_user_message(chat_id: str, user_id: str, text: str) -> None:
    (
        supabase()
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


def _persist_assistant_message(
    chat_id: str, user_id: str, text: str, citations: list[dict]
) -> dict:
    res = (
        supabase()
        .table("chat_messages")
        .insert(
            {
                "chat_id": chat_id,
                "user_id": user_id,
                "role": "assistant",
                "content": text,
                "citations": citations,
            }
        )
        .execute()
    )
    return res.data[0]


def _load_corpus_name(project_id: str) -> str | None:
    row = (
        supabase()
        .table("projects")
        .select("rag_corpus_name")
        .eq("id", project_id)
        .single()
        .execute()
    )
    return (row.data or {}).get("rag_corpus_name")


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


@traceable(run_type="chain", name="chats.send_message")
async def _send_message_stream(
    *,
    chat: dict,
    text: str,
    chat_id: str,
    user_id: str,
    template: list[str] | None,
):
    """ADK-driven streaming chat turn (plan 19.0)."""
    project_id = chat["project_id"]
    debug_trace = await asyncio.to_thread(_is_debug_user, user_id)

    # 1. Persist user message.
    await asyncio.to_thread(_persist_user_message, chat_id, user_id, text)

    # 2. Resolve corpus.
    corpus_name = await asyncio.to_thread(_load_corpus_name, project_id)
    if not corpus_name:
        yield "data: " + json.dumps(
            {"type": "delta", "content": "_Bitte zuerst Dokumente hochladen._"}
        ) + "\n\n"
        yield "data: " + json.dumps({"type": "meta", "citations": []}) + "\n\n"
        yield "data: " + json.dumps({"type": "done"}) + "\n\n"
        return

    # 3. Get-or-build the per-corpus AdkApp; seed a fresh session with replayed history.
    try:
        app = await get_or_build_app(corpus_name)
        session = await seed_session(app=app, user_id=user_id, chat_id=chat_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("adk session build failed: %s", exc)
        yield "data: " + json.dumps(
            {"type": "delta", "content": _friendly_gemini_error(exc)}
        ) + "\n\n"
        yield "data: " + json.dumps({"type": "meta", "citations": []}) + "\n\n"
        yield "data: " + json.dumps({"type": "done"}) + "\n\n"
        return

    answer_parts: list[str] = []
    handed_off = False
    trace_id = 0
    try:
        async for event in app.async_stream_query(
            message=text,
            session_id=session.id,
            user_id=user_id,
        ):
            kind = event_kind(event)
            author = event_author(event)

            # Debug-only: emit a trace frame for every event before any
            # SSE-shape filtering. Frontend gates display behind the same
            # debug flag, but we keep the bytes off the wire for prod
            # accounts to avoid leaking agent internals.
            if debug_trace:
                trace_id += 1
                frame = _build_trace_frame(event, event_id=trace_id)
                if frame is not None:
                    yield "data: " + json.dumps(frame) + "\n\n"

            # Forward only orchestrator model-text events; sub-agent
            # intermediate output stays inside the agent tree.
            if kind == "model_text" and author == "chat_orchestrator":
                piece = event_text(event)
                if piece:
                    answer_parts.append(piece)
                    yield "data: " + json.dumps(
                        {"type": "delta", "content": piece}
                    ) + "\n\n"

            elif kind == "tool_response" and is_v2_handoff(event):
                handed_off = True
                async for sse in stream_projektanalyse_v2(
                    template=template, chat_id=chat_id, user_id=user_id
                ):
                    yield sse
                return
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "adk stream failed: %s: %s", type(exc).__name__, exc, exc_info=True
        )
        notice = _friendly_gemini_error(exc)
        if answer_parts:
            yield "data: " + json.dumps(
                {"type": "delta", "content": "\n\n" + notice}
            ) + "\n\n"
        else:
            yield "data: " + json.dumps(
                {"type": "delta", "content": notice}
            ) + "\n\n"
        yield "data: " + json.dumps({"type": "meta", "citations": []}) + "\n\n"
        yield "data: " + json.dumps({"type": "done"}) + "\n\n"
        return

    if handed_off:
        return

    # 4. Read accumulated citations from session state, dedupe + renumber.
    sess_service = app._tmpl_attrs["session_service"]
    app_name = app._tmpl_attrs["app_name"]
    final_session = await sess_service.get_session(
        app_name=app_name, user_id=user_id, session_id=session.id
    )
    raw_citations = list((final_session.state or {}).get("citations", []))
    final_citations, remap = dedupe_and_renumber(raw_citations)
    raw_answer = "".join(answer_parts)
    annotated = rewrite_refs(raw_answer, remap)

    yield "data: " + json.dumps(
        {"type": "meta", "citations": final_citations, "content": annotated}
    ) + "\n\n"

    persisted = annotated.strip()
    if persisted:
        msg = await asyncio.to_thread(
            _persist_assistant_message,
            chat_id,
            user_id,
            persisted,
            final_citations,
        )
        yield "data: " + json.dumps(
            {"type": "done", "message_id": msg["id"]}
        ) + "\n\n"
    else:
        yield "data: " + json.dumps({"type": "done"}) + "\n\n"


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
    # LANGSMITH_API_KEY is set.
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
