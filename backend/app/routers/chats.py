"""Chat endpoints — ADK multi-agent edition.

A chat turn = one orchestrator-driven AdkApp run. The orchestrator
(gemini-2.5-flash) routes to:
  - rag_specialist (per-question Flash worker) — einzelne Projektfrage
  - dispatch_rag_questions (FunctionTool) — 2+ unabhaengige Projektfragen
  - run_projektanalyse (FunctionTool) — Vorlage des Nutzers durchgehen
  - web_researcher (Flash + Google search + UrlContext) — externe Fragen

Per-turn lifecycle:
  1. Persist user message to chat_messages.
  2. Resolve corpus -> get-or-build a per-corpus AdkApp (LRU-cached).
  3. Seed a fresh in-memory ADK session with replayed Supabase history.
  4. Stream events; forward orchestrator model_text deltas to SSE.
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

import re

from app.adk.app_factory import get_or_build_app
from app.adk.citation_aggregator import dedupe_and_renumber, rewrite_refs
from app.adk._harpoon_retry import (
    DEFAULT_MAX_ATTEMPTS as _RETRY_MAX_ATTEMPTS,
    harpoon_backoff_delay as _retry_backoff_delay,
    is_harpoon_transient as _is_transient_upstream,
)
from app.adk.dispatch_rag_questions_tool import DISPATCH_PROGRESS_CHAN
from app.adk.event_translator import (
    event_author,
    event_kind,
    event_state_delta,
    event_text,
)
from app.adk.history import seed_session
from app.auth import current_user_id
from app.config import settings
from app.db import supabase
from app.gemini_client import gemini_client_untraced

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


def _dump_rag_state(corpus_name: str | None, project_id: str) -> dict:
    """Snapshot the RAG state from BOTH our DB and Vertex's view, so error
    logs make it obvious whether a Google-side rejection lines up with our
    'ready' bookkeeping or contradicts it. Never raises — best-effort."""
    out: dict = {"corpus_name": corpus_name, "project_id": project_id}
    # Our DB view: project_files statuses.
    try:
        rows = (
            supabase()
            .table("project_files")
            .select("id,filename,status,ingest_error,rag_file_name,page_count")
            .eq("project_id", project_id)
            .execute()
        )
        out["db_files"] = [
            {
                "id": r["id"],
                "filename": r.get("filename"),
                "status": r.get("status"),
                "page_count": r.get("page_count"),
                "rag_file_name": r.get("rag_file_name"),
                "ingest_error": r.get("ingest_error"),
            }
            for r in (rows.data or [])
        ]
    except Exception as e:
        out["db_files_error"] = f"{type(e).__name__}: {e}"
    # Vertex's view: list_files on the corpus + state.
    if corpus_name:
        try:
            from vertexai import rag
            from app.rag_corpus import _init_vertex
            _init_vertex()
            vfiles = list(rag.list_files(corpus_name=corpus_name))
            out["vertex_files"] = [
                {
                    "name": getattr(f, "name", None),
                    "display": getattr(f, "display_name", None),
                    "state": getattr(f, "rag_file_state", None) or getattr(f, "state", None),
                }
                for f in vfiles
            ]
        except Exception as e:
            out["vertex_files_error"] = f"{type(e).__name__}: {e}"
    return out



_TRACE_TEXT_PREVIEW_LIMIT = 600
_TRACE_ARGS_PREVIEW_LIMIT = 400


# Parses one row of web_researcher's mandated Quellen block:
#   [N] https://example.com/foo — Title here
# Em-dash, regular dash, or colon are all accepted as the title separator.
_WEB_QUELLE_RE = re.compile(
    r"^\s*\[(\d+)\]\s*(https?://\S+)\s*[—\-:|]\s*(.+?)\s*$",
    re.MULTILINE,
)


_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n+")


def _normalize_for_dedupe(s: str) -> str:
    """Strip [N] markers + whitespace + punctuation noise so two paragraphs
    that differ only in citation indices compare equal. Used by
    `_dedupe_repeated_paragraphs` — we keep the original text and only use
    this for comparison."""
    s = re.sub(r"\[\d+\]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip().casefold()


def _dedupe_repeated_paragraphs(text: str) -> str:
    """Drop near-identical paragraphs that the orchestrator occasionally
    emits twice in one turn — once with rag_specialist's local `[N]`
    markers, once re-stated with global ones. Compare paragraphs after
    stripping [N] markers / whitespace; on a match, keep the FIRST
    occurrence (which has the correct streamed-text alignment) and drop
    the duplicate. Single-paragraph text is returned unchanged.

    Threshold is full-string equality after normalisation — we don't
    want fuzzy matching to suppress legitimate restated points."""
    if not text or "\n\n" not in text:
        return text
    paragraphs = _PARAGRAPH_SPLIT_RE.split(text)
    if len(paragraphs) < 2:
        return text
    seen: set[str] = set()
    out: list[str] = []
    for p in paragraphs:
        key = _normalize_for_dedupe(p)
        # Skip the empty/whitespace-only or trivial keys (e.g. just a
        # punctuation mark) — those legitimately repeat as separators.
        if len(key) < 12:
            out.append(p)
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return "\n\n".join(out)


def _filename_from_uri(uri: str) -> str:
    """Best-effort filename from a gs:// or https://... URI: just the basename
    after the last '/', without extension stripping."""
    if not uri:
        return ""
    # Drop trailing slash, drop query/fragment.
    cleaned = uri.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    if "/" not in cleaned:
        return cleaned
    return cleaned.rsplit("/", 1)[-1]


def _ids_from_gs_uri(uri: str) -> tuple[str | None, str | None]:
    """Extract (project_id, file_id) from the canonical GCS object key.

    The ingest pipeline pins the layout to
    `gs://{bucket}/{user_id}/{project_id}/{file_id}/{sanitized}.pdf`
    (see `app/gcs.py::object_key`). The frontend's PDF viewer needs both
    ids to call `/api/projects/{pid}/files/{fid}/signed-url`, but Vertex
    grounding only returns the URI. Parsing the layout locally avoids a
    DB lookup per citation.

    Returns (None, None) if the URI doesn't fit the layout.
    """
    if not uri or not uri.startswith("gs://"):
        return (None, None)
    rest = uri[len("gs://"):]
    parts = rest.split("/", 4)
    # [bucket, user_id, project_id, file_id, basename] — exactly 5 segments.
    if len(parts) < 5:
        return (None, None)
    _bucket, _user_id, project_id, file_id, _basename = parts
    if not project_id or not file_id:
        return (None, None)
    return (project_id, file_id)


def _citations_from_grounding(chunks: list[dict]) -> list[dict]:
    """Translate `state["agent_grounding_chunks"]` entries (written by
    StreamingAgentTool from Vertex `GroundingMetadata.grounding_chunks`)
    into the citation-record shape the rest of the chat pipeline expects.

    Citation record shape (matches the previous custom-tool version):
        {idx, kind="file", filename, snippet, file_id, chunk_id, score,
         uri, title}

    `idx` is 1-based in retrieval order — the rag_specialist's instruction
    tells the model to emit [N] markers in the same order. `chunk_id` is
    stable per (uri, snippet[:120]) so two retrievals of the same chunk
    across a multi-question fan-out collapse to one citation, while two
    different passages from the same file stay distinct (no chunk_id is
    surfaced by the Vertex GroundingChunk schema, so we synthesise one
    that matches dedupe_and_renumber's `(file_id, chunk_id)` key).
    """
    import hashlib

    out: list[dict] = []
    for i, c in enumerate(chunks, start=1):
        if not isinstance(c, dict):
            continue
        uri = c.get("uri") or ""
        title = c.get("title") or ""
        text = c.get("rag_chunk_text") or c.get("text") or ""
        filename = title or _filename_from_uri(uri) or "Dokument"
        text_key = (text or "")[:120]
        h = hashlib.sha1(text_key.encode("utf-8")).hexdigest()[:12]
        chunk_id = f"rag:{uri}:{h}" if (uri or text_key) else f"rag:chunk:{i}"
        project_id, file_id = _ids_from_gs_uri(uri)
        out.append({
            "idx": i,
            "kind": "file",
            "filename": filename,
            "snippet": text,
            # file_id was previously the raw gs:// URI, which the PDF viewer
            # endpoint can't resolve. We now parse the canonical GCS layout
            # to surface the real DB row id + project_id.
            "file_id": file_id,
            "project_id": project_id,
            "chunk_id": chunk_id,
            # Per-chunk confidence aggregated from grounding_supports
            # (StreamingAgentTool._per_chunk_confidence). Higher = better;
            # None means the chunk was retrieved but never grounded a
            # claim in the final answer.
            "score": c.get("confidence"),
            "uri": uri,
            "title": title,
        })
    return out


def _extract_web_citations(text: str) -> list[dict]:
    """Parse a web_researcher tool_response 'result' string into citation
    records. Returns [] if no Quellen block is found (e.g. 'im Web nicht
    belegt' answers).

    Each record gets kind='web', local idx (matching the [N] markers the
    orchestrator forwards verbatim into the final answer), url, title,
    domain, and a synthesised chunk_id stable across turns so the
    frontend's dedupe-by-chunk_id logic continues to work.
    """
    if not text:
        return []
    out: list[dict] = []
    seen_idx: set[int] = set()
    for m in _WEB_QUELLE_RE.finditer(text):
        try:
            idx = int(m.group(1))
        except ValueError:
            continue
        if idx in seen_idx:
            continue
        seen_idx.add(idx)
        url = m.group(2).rstrip(".,;)")
        title = m.group(3).strip()
        # Strip a trailing closing-bracket / period the LLM occasionally
        # tacks on after the title.
        title = re.sub(r"[\.\)\]]+$", "", title).strip()
        domain = re.sub(r"^https?://", "", url).split("/", 1)[0]
        out.append({
            "idx": idx,
            "kind": "web",
            "url": url,
            "title": title or domain,
            "domain": domain,
            "chunk_id": f"web:{url}",
            "filename": title or domain,
            "uri": url,
            "file_id": None,
            "score": None,
            "snippet": title or url,
        })
    return out


def _web_response_text(event: dict) -> str | None:
    """If event is a tool_response from web_researcher, return its 'result'
    text (or the stringified response body). None otherwise."""
    if event_kind(event) != "tool_response":
        return None
    parts = (event.get("content") or {}).get("parts") or []
    for p in parts:
        fr = p.get("function_response") or {}
        if fr.get("name") != "web_researcher":
            continue
        body = fr.get("response") or {}
        if isinstance(body, dict):
            val = body.get("result")
            if isinstance(val, str):
                return val
            return json.dumps(body, ensure_ascii=False)
        return str(body)
    return None


def _build_trace_frames(event: dict, *, next_id: int) -> list[dict]:
    """Reduce one ADK event dict into one OR MORE trace frames for the UI.

    A single ADK event can carry MULTIPLE function_calls or function_responses
    in its `parts` array — that's how parallel tool fan-out is encoded
    (e.g. orchestrator dispatches 11 rag_specialist calls in one model
    response). Earlier we collapsed to the first part; that hid 10/11
    parallel calls in the activity panel.

    Shape per frame:
      {type: "trace", id, author, kind,
       name?,        # tool name for tool_call / tool_response
       args?,        # truncated tool_call arguments (str)
       response?,    # truncated tool_response body (str)
       text?}        # truncated model text preview
    """
    kind = event_kind(event)
    if kind == "other":
        return []
    author = event_author(event) or "unknown"
    parts = (event.get("content") or {}).get("parts") or []
    out: list[dict] = []

    def _new_frame() -> dict:
        return {
            "type": "trace",
            "id": f"evt-{next_id + len(out)}",
            "author": author,
            "kind": kind,
        }

    if kind == "tool_call":
        for p in parts:
            fc = p.get("function_call")
            if not fc:
                continue
            f = _new_frame()
            # Use Gemini's function_call.id (the same id is echoed back on
            # the matching function_response) as the trace row id, so the
            # tool_response frame upserts onto the tool_call frame and the
            # UI shows ONE row that flips status — same UX as the batched
            # dispatch frames. Falls back to the auto-assigned evt-id when
            # the SDK doesn't surface an id.
            fc_id = fc.get("id")
            if fc_id:
                f["id"] = f"tool-{fc_id}"
            f["name"] = fc.get("name")
            f["args"] = json.dumps(fc.get("args") or {}, ensure_ascii=False)[
                :_TRACE_ARGS_PREVIEW_LIMIT
            ]
            out.append(f)
    elif kind == "tool_response":
        for p in parts:
            fr = p.get("function_response")
            if not fr:
                continue
            f = _new_frame()
            fr_id = fr.get("id")
            if fr_id:
                f["id"] = f"tool-{fr_id}"
            tool_name = fr.get("name")
            f["name"] = tool_name
            body = fr.get("response") or {}
            # search_project_documents gets a richer payload so the activity
            # panel can render retrieved chunks + confidence scores instead
            # of a 400-char-truncated JSON dump. Other tools keep the
            # generic truncated-response field.
            if (
                tool_name == "search_project_documents"
                and isinstance(body, dict)
            ):
                f["status"] = body.get("status")
                f["chunks"] = [
                    {
                        "idx": c.get("idx"),
                        "filename": c.get("filename"),
                        "score": c.get("score"),
                        "snippet": (c.get("text") or "")[:240],
                    }
                    for c in (body.get("chunks") or [])
                ]
            else:
                f["response"] = json.dumps(body, ensure_ascii=False)[
                    :_TRACE_ARGS_PREVIEW_LIMIT
                ]
            out.append(f)
    elif kind == "model_text":
        # The orchestrator's model_text is already streamed verbatim into
        # the chat as the user-facing answer (see the "model_text and author
        # == chat_orchestrator" branch in _assistant_turn_events). Surfacing
        # it again as a trace row would be a duplicate. Sub-agent model_text
        # never reaches the chat surface, so we keep it for the activity
        # panel.
        if author == "chat_orchestrator":
            return out
        answer = event_text(event)
        if answer:
            f = _new_frame()
            f["text"] = answer[:_TRACE_TEXT_PREVIEW_LIMIT]
            out.append(f)
    return out


def _build_sub_agent_trace_frames(
    state_delta: dict,
    *,
    seen: set[int],
    next_id: int,
) -> tuple[list[dict], set[int]]:
    """Translate `state["agent_trace"]` deltas into activity-panel frames.

    StreamingAgentTool appends entries shaped
        {"agent": <author>, "kind": "model_thought" | "tool_call"
                                    | "tool_response",
         "seq": <int>, ...kind-specific fields}
    Each new seq becomes one trace frame, preserving the sub-agent name as
    the frame's `author` so e.g. document_retriever's calls show up under
    its own row instead of being mis-attributed to rag_specialist.
    Callers track `seen` across the turn to avoid re-emitting the same
    entry when subsequent state deltas re-deliver the cumulative list (ADK
    forwards the whole state, not a diff).
    """
    raw = state_delta.get("agent_trace") or []
    if not raw:
        return [], seen

    out: list[dict] = []
    seen = set(seen)
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        seq = entry.get("seq")
        if seq is None or seq in seen:
            continue
        seen.add(seq)
        kind = entry.get("kind")
        author = entry.get("agent") or "unknown"
        frame: dict = {
            "type": "trace",
            "id": f"evt-{next_id + len(out)}",
            "author": author,
            "kind": kind,
        }
        if kind == "model_thought":
            # Sub-agents no longer emit thoughts (see _THINKING_CONFIG in
            # adk/agents.py); skip any stale entries so the activity panel
            # stays clean.
            continue
        if kind == "tool_call":
            # Stable id from function_call.id (set by StreamingAgentTool's
            # _capture_activity) so the matching tool_response upserts onto
            # this row in the UI. Falls back to the auto evt-id when ADK
            # doesn't surface one.
            cid = entry.get("call_id")
            if cid:
                frame["id"] = f"tool-{cid}"
            frame["name"] = entry.get("name")
            frame["args"] = (entry.get("args") or "")[:_TRACE_ARGS_PREVIEW_LIMIT]
        elif kind == "tool_response":
            cid = entry.get("call_id")
            if cid:
                frame["id"] = f"tool-{cid}"
            tool_name = entry.get("name")
            frame["name"] = tool_name
            body = entry.get("response") or {}
            # Mirror the rich-payload treatment from the parent
            # `_build_trace_frames`: search_project_documents gets chunks +
            # scores instead of a 400-char-truncated JSON dump.
            if (
                tool_name == "search_project_documents"
                and isinstance(body, dict)
            ):
                frame["status"] = body.get("status")
                frame["chunks"] = [
                    {
                        "idx": c.get("idx"),
                        "filename": c.get("filename"),
                        "score": c.get("score"),
                        "snippet": (c.get("text") or "")[:240],
                    }
                    for c in (body.get("chunks") or [])
                ]
            else:
                frame["response"] = json.dumps(body, ensure_ascii=False)[
                    :_TRACE_ARGS_PREVIEW_LIMIT
                ]
        else:
            continue
        out.append(frame)
    return out, seen


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


class TitleIn(BaseModel):
    first_message: str


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    citations: list[dict] | None = None
    traces: list[dict] | None = None
    status: str = "done"
    error: str | None = None


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


def _persist_user_message(chat_id: str, user_id: str, text: str) -> dict:
    res = (
        supabase()
        .table("chat_messages")
        .insert(
            {
                "chat_id": chat_id,
                "user_id": user_id,
                "role": "user",
                "content": text,
                "status": "done",
            }
        )
        .execute()
    )
    return res.data[0]


def _create_streaming_assistant(chat_id: str, user_id: str) -> dict:
    """Insert the assistant row up front with status='streaming' so the turn
    has a stable id the frontend can subscribe to via Realtime. Content stays
    empty until _finalize_assistant_message rewrites it with the annotated
    final answer."""
    res = (
        supabase()
        .table("chat_messages")
        .insert(
            {
                "chat_id": chat_id,
                "user_id": user_id,
                "role": "assistant",
                "content": "",
                "citations": [],
                "status": "streaming",
            }
        )
        .execute()
    )
    return res.data[0]


def _insert_delta(
    *,
    message_id: str,
    chat_id: str,
    user_id: str,
    seq: int,
    payload: dict,
) -> None:
    """Append one event payload to chat_message_deltas. The frontend
    subscribes via Realtime and replays missed events by message_id."""
    (
        supabase()
        .table("chat_message_deltas")
        .insert(
            {
                "message_id": message_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "seq": seq,
                "payload": payload,
            }
        )
        .execute()
    )


def _finalize_assistant_message(
    *,
    message_id: str,
    content: str,
    citations: list[dict],
    traces: list[dict] | None,
    error: str | None,
) -> None:
    """Update the streaming assistant row to its terminal state. status flips
    to 'done' (success) or 'error'; content + citations + traces carry the
    final annotated answer + activity-panel snapshot the frontend renders on
    chat reopen / hard reload."""
    status = "error" if error else "done"
    (
        supabase()
        .table("chat_messages")
        .update(
            {
                "content": content,
                "citations": citations,
                "traces": traces,
                "status": status,
                "error": error,
            }
        )
        .eq("id", message_id)
        .execute()
    )


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
        .select("id,role,content,citations,traces,status,error")
        .eq("chat_id", chat_id)
        .eq("user_id", user_id)
        .order("created_at")
        .execute()
    )
    return [
        MessageOut(
            id=r["id"],
            role=r["role"],
            content=r["content"],
            citations=r.get("citations"),
            traces=r.get("traces"),
            status=r.get("status") or "done",
            error=r.get("error"),
        )
        for r in (res.data or [])
    ]


@traceable(run_type="chain", name="chats.send_message")
async def _assistant_turn_events(
    *,
    chat: dict,
    text: str,
    chat_id: str,
    user_id: str,
):
    """ADK-driven assistant turn. Yields payload dicts (delta / trace /
    progress / meta / done). The caller is responsible for persisting the
    user message and routing payloads to chat_message_deltas + finalizing
    the assistant row — this generator no longer touches chat_messages."""
    import time as _time
    t_chain_start = _time.time()
    project_id = chat["project_id"]

    # 1. Resolve corpus.
    corpus_name = await asyncio.to_thread(_load_corpus_name, project_id)
    log.info("chat[%s]: corpus=%s (resolve %.2fs)", chat_id, corpus_name, _time.time() - t_chain_start)
    if not corpus_name:
        yield {"type": "delta", "content": "_Bitte zuerst Dokumente hochladen._"}
        yield {"type": "meta", "citations": [], "content": "_Bitte zuerst Dokumente hochladen._"}
        yield {"type": "done"}
        return

    # 2. Get-or-build the per-corpus AdkApp; seed a fresh session with replayed history.
    try:
        t_app = _time.time()
        app = await get_or_build_app(corpus_name)
        log.info("chat[%s]: app ready (%.2fs)", chat_id, _time.time() - t_app)
        t_seed = _time.time()
        session = await seed_session(app=app, user_id=user_id, chat_id=chat_id)
        log.info("chat[%s]: session seeded (%.2fs); starting stream", chat_id, _time.time() - t_seed)
    except Exception as exc:  # noqa: BLE001
        log.exception("adk session build failed: %s", exc)
        notice = _friendly_gemini_error(exc)
        yield {"type": "delta", "content": notice}
        yield {"type": "meta", "citations": [], "content": notice}
        yield {"type": "done"}
        return

    answer_parts: list[str] = []
    web_response_texts: list[str] = []
    trace_id = 0
    # Per-turn high-water mark for sub-agent activity. StreamingAgentTool
    # writes into session state["agent_trace"] (thoughts + tool_calls +
    # tool_responses); the parent runner sees the cumulative list on every
    # state_delta, so we track which seq ids we've already rendered to
    # avoid duplicates.
    seen_trace_seqs: set[int] = set()
    t_stream = _time.time()
    first_event = True
    # Cap LLM calls per turn. Default RunConfig.max_llm_calls=500 leaves
    # room for the orchestrator to enter dispatch loops (e.g. re-firing
    # dispatch_rag_questions on the same 11-question batch multiple times,
    # observed 2026-05-02 as 3x identical fan-out clusters in audit logs).
    # 8 budgets: 1 orchestrator routing turn + 1 dispatch_rag_questions
    # call (which internally fans out N rag_specialist runs, each counted
    # separately by the runner) + 1 final summarization, with slack for a
    # legitimate single follow-up tool call. If a turn legitimately needs
    # more, it surfaces as a clean RunConfig limit error instead of a
    # silent multi-minute loop.
    _run_config = {"max_llm_calls": 8}

    # Per-question dispatch progress: `dispatch_rag_questions` tool pushes
    # {"phase":"start"|"done"|"error", "idx", "question", ...} dicts onto
    # this queue as each sub-rag_specialist call moves through its lifecycle.
    # We set it on a ContextVar before launching the ADK pump task so that
    # all downstream tasks (adk runner -> tool invocation -> asyncio.gather
    # children) inherit the channel via Python's standard contextvar-copy-
    # on-Task-create semantics. Without live progress, an 11-question
    # fan-out renders as a single opaque tool_call in the UI for ~30s+.
    dispatch_q: asyncio.Queue = asyncio.Queue()
    _chan_token = DISPATCH_PROGRESS_CHAN.set(dispatch_q)

    # Merge ADK's event stream and the dispatch_q into one queue so the
    # consumer below sees them in arrival order. Two pump tasks feed the
    # merged queue; the consumer drains it until it sees an `end` or
    # `error` envelope.
    merged_q: asyncio.Queue = asyncio.Queue()

    # Silent retry-with-backoff over the WHOLE turn for transient upstream
    # failures (Vertex Harpoon FAILED_PRECONDITION, 503 UNAVAILABLE, 429
    # RESOURCE_EXHAUSTED, 500 INTERNAL, DEADLINE_EXCEEDED, socket resets, …).
    # Predicate + budget come from `app.adk._harpoon_retry`. Goal: the
    # frontend keeps showing its loading dots through transient capacity
    # pressure, never a "could not generate" toast. Retries fire only
    # before any event has reached the SSE stream — once content has
    # shipped, retrying would double up, so we bail.

    async def _pump_adk_events():
        nonlocal session
        emitted_any = False
        last_exc: Exception | None = None
        for attempt in range(_RETRY_MAX_ATTEMPTS):
            try:
                async for ev in app.async_stream_query(
                    message=text,
                    session_id=session.id,
                    user_id=user_id,
                    run_config=_run_config,
                ):
                    emitted_any = True
                    await merged_q.put({"_type": "adk", "event": ev})
                last_exc = None
                break  # success
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if emitted_any:
                    log.warning(
                        "chat[%s]: stream errored after emitting events; not retrying. %s: %s",
                        chat_id, type(exc).__name__, exc,
                    )
                    break
                if not _is_transient_upstream(exc):
                    break
                if attempt + 1 >= _RETRY_MAX_ATTEMPTS:
                    log.warning(
                        "chat[%s]: upstream retries exhausted (%d/%d); giving up. %s",
                        chat_id, attempt + 1, _RETRY_MAX_ATTEMPTS, exc,
                    )
                    break
                delay = _retry_backoff_delay(attempt)
                log.warning(
                    "chat[%s]: upstream transient (%s); retry %d/%d in %.1fs. detail=%s",
                    chat_id, type(exc).__name__,
                    attempt + 1, _RETRY_MAX_ATTEMPTS, delay,
                    str(exc).split("Events {")[0][:200],
                )
                await asyncio.sleep(delay)
                # Re-seed the session so the retry starts from a clean ADK
                # state. seed_session replays history from Supabase and drops
                # the trailing user row (already passed via `message=text`),
                # so this is idempotent — the model sees identical input.
                try:
                    session = await seed_session(
                        app=app, user_id=user_id, chat_id=chat_id
                    )
                except Exception as seed_exc:  # noqa: BLE001
                    log.warning(
                        "chat[%s]: re-seed failed during retry: %s",
                        chat_id, seed_exc,
                    )
                    last_exc = seed_exc
                    break
        if last_exc is not None:
            await merged_q.put({"_type": "error", "exc": last_exc})
        await merged_q.put({"_type": "end"})

    async def _pump_dispatch_progress():
        while True:
            msg = await dispatch_q.get()
            await merged_q.put({"_type": "dispatch", **msg})

    adk_task = asyncio.create_task(_pump_adk_events())
    disp_task = asyncio.create_task(_pump_dispatch_progress())

    try:
        while True:
            envelope = await merged_q.get()
            etype = envelope["_type"]
            if etype == "end":
                break
            if etype == "error":
                raise envelope["exc"]
            if etype == "dispatch":
                # Per-question progress -> trace frame in the existing SSE
                # schema. We emit ONE id per question (`dispatch-<idx>`) and
                # let later frames overwrite earlier ones in the frontend
                # via id-based upsert — so a question stays as a single row
                # that flips from `laeuft` -> `fertig` in place. Backend
                # always sends the question text in `args` so the start
                # frame's question survives even after the done frame's
                # `response` overwrites the row.
                phase = envelope.get("phase")
                idx = envelope.get("idx", 0)
                question = envelope.get("question") or ""
                step_label = f"Frage {idx + 1}"
                trace_id += 1
                row_id = f"dispatch-{idx}"
                args_blob = json.dumps({"question": question}, ensure_ascii=False)
                if phase == "start":
                    yield {
                        "type": "trace",
                        "id": row_id,
                        "author": "rag_specialist",
                        "kind": "tool_call",
                        "name": step_label,
                        "args": args_blob,
                    }
                elif phase == "done":
                    answer = envelope.get("answer") or ""
                    yield {
                        "type": "trace",
                        "id": row_id,
                        "author": "rag_specialist",
                        "kind": "tool_response",
                        "name": step_label,
                        "args": args_blob,
                        "response": json.dumps(
                            {"question": question, "answer": answer},
                            ensure_ascii=False,
                        ),
                        "status": "ok",
                    }
                elif phase == "error":
                    yield {
                        "type": "trace",
                        "id": row_id,
                        "author": "rag_specialist",
                        "kind": "tool_response",
                        "name": step_label,
                        "args": args_blob,
                        "response": json.dumps(
                            {"question": question, "error": envelope.get("error")},
                            ensure_ascii=False,
                        ),
                        "status": "error",
                    }
                continue

            # `etype == "adk"` from here on.
            event = envelope["event"]
            if first_event:
                log.info("chat[%s]: first ADK event after %.2fs", chat_id, _time.time() - t_stream)
                first_event = False
            kind = event_kind(event)
            author = event_author(event)
            log.info("chat[%s]: event kind=%s author=%s (t+%.1fs)", chat_id, kind, author, _time.time() - t_stream)

            # Diagnostic: surface empty-content events from the orchestrator
            # post-tool LLM call (suspected adk-python #3525 — Gemini 2.5
            # Flash sometimes returns Content(parts=None) after a function
            # response, which collapses the turn into a silent stream end).
            _ev_content = getattr(event, "content", None)
            if _ev_content is not None:
                _parts = getattr(_ev_content, "parts", None)
                _role = getattr(_ev_content, "role", None)
                _finish = getattr(event, "finish_reason", None)
                _block = getattr(event, "block_reason", None)
                log.info(
                    "chat[%s]: event content author=%s role=%s parts_len=%s finish=%s block=%s",
                    chat_id, author, _role,
                    len(_parts) if _parts else 0,
                    _finish, _block,
                )

            # Emit one trace frame per function_call / function_response
            # part (parallel fan-out is encoded as N parts within a single
            # ADK event), plus one per model_text event. Order matters for
            # the activity panel:
            # 1) sub-agent thoughts FIRST so they render BEFORE the
            #    tool_response that delivered them (chronologically the
            #    sub-agent thought before producing its result).
            # 2) then the event's own trace frames (which already include
            #    the orchestrator's own thoughts inline via
            #    `_build_trace_frames`'s prelude — those land before the
            #    tool_call/model_text frame in the same event).
            sub_frames, seen_trace_seqs = _build_sub_agent_trace_frames(
                event_state_delta(event),
                seen=seen_trace_seqs,
                next_id=trace_id + 1,
            )
            for f in sub_frames:
                trace_id += 1
                yield f
            frames = _build_trace_frames(event, next_id=trace_id + 1)
            for f in frames:
                trace_id += 1
                yield f

            # Forward only orchestrator model-text events; sub-agent
            # intermediate output stays inside the agent tree.
            if kind == "model_text" and author == "chat_orchestrator":
                piece = event_text(event)
                if piece:
                    answer_parts.append(piece)
                    yield {"type": "delta", "content": piece}

            # Capture web_researcher tool_response text so we can parse the
            # mandated Quellen: block into citation records after the run.
            # The orchestrator forwards web's local [N] markers verbatim, so
            # we keep them as-is (offset only when rag also fired — see
            # post-run merge below).
            if kind == "tool_response":
                web_text = _web_response_text(event)
                if web_text:
                    web_response_texts.append(web_text)
    except Exception as exc:  # noqa: BLE001
        # Cancel the dispatch pump so it doesn't leak into the next turn;
        # the ADK pump is already finished if we got here via the `error`
        # envelope, but cancel it defensively in case the exception came
        # from inside the consumer loop instead.
        for _t in (adk_task, disp_task):
            if not _t.done():
                _t.cancel()
        DISPATCH_PROGRESS_CHAN.reset(_chan_token)
        log.warning(
            "adk stream failed: %s: %s", type(exc).__name__, exc, exc_info=True
        )
        # Snapshot RAG state so we can correlate "we say ready" vs "Vertex says
        # X" without having to repro. Best-effort — never raises.
        try:
            rag_state = await asyncio.to_thread(
                _dump_rag_state, corpus_name, project_id
            )
            log.warning(
                "adk stream failed — rag state for chat=%s:\n%s",
                chat_id, json.dumps(rag_state, indent=2, default=str),
            )
        except Exception as dump_exc:  # noqa: BLE001
            log.warning("rag state dump failed: %s", dump_exc)
        notice = _friendly_gemini_error(exc)
        if answer_parts:
            yield {"type": "delta", "content": "\n\n" + notice}
            final_text = "".join(answer_parts) + "\n\n" + notice
        else:
            yield {"type": "delta", "content": notice}
            final_text = notice
        yield {"type": "meta", "citations": [], "content": final_text}
        yield {"type": "done"}
        return
    else:
        # Success path: cancel the dispatch pump and reset the contextvar.
        # adk_task is already done (it pushed the `end` envelope that broke
        # us out of the consumer loop); disp_task is still parked on
        # dispatch_q.get().
        for _t in (adk_task, disp_task):
            if not _t.done():
                _t.cancel()
        DISPATCH_PROGRESS_CHAN.reset(_chan_token)

    # 4. Build citation records from grounding metadata. With native vertex_
    # rag_store retrieval, the rag_specialist's tool_context.state no longer
    # holds citation rows directly — instead StreamingAgentTool propagates
    # `agent_grounding_chunks` (a flat list of {text, title, uri, ...}
    # entries, in retrieval order, accumulated across multi-question fan-
    # outs). We turn each chunk into a [N] citation record the existing
    # aggregator + frontend already know how to render.
    sess_service = app._tmpl_attrs["session_service"]
    app_name = app._tmpl_attrs["app_name"]
    final_session = await sess_service.get_session(
        app_name=app_name, user_id=user_id, session_id=session.id
    )
    state = final_session.state or {}
    raw_citations = _citations_from_grounding(
        state.get("agent_grounding_chunks") or []
    )

    # Web citations: web_researcher's local [N] are offset-free in pure-web
    # turns (no rag → state empty → no collision). In a mixed turn (rag +
    # web), web's [N] would collide with rag's idx; we offset by current
    # state length, but the orchestrator's preserved web markers in the
    # forwarded text won't auto-remap. Logged as a known limitation —
    # rare in practice (web is normally a user-elected follow-up turn).
    if web_response_texts:
        offset = len(raw_citations)
        if offset:
            log.info(
                "mixed rag+web turn: %d existing citations, offsetting web by %d",
                offset, offset,
            )
        for txt in web_response_texts:
            for rec in _extract_web_citations(txt):
                rec["idx"] = rec["idx"] + offset
                raw_citations.append(rec)

    final_citations, remap = dedupe_and_renumber(raw_citations)
    raw_answer = "".join(answer_parts)
    annotated = rewrite_refs(raw_answer, remap)
    # Drop near-identical paragraphs the orchestrator sometimes emits twice
    # (once with rag_specialist's local [N] markers, once re-stated with
    # globals). Empirically observed on Q9/Q11 of the 2026-05-02 11-question
    # judge run.
    annotated = _dedupe_repeated_paragraphs(annotated)
    # Backstop: when the orchestrator skips retrieval entirely and answers
    # from history, it can still emit [N] markers it remembers from prior
    # turns. Those indices are meaningless in this turn (final_citations is
    # empty -> no chip list shown), so strip them rather than ship a text
    # peppered with dead [5]/[10] references that the user has no way to
    # resolve. The instruction-level fix (KONTEXT-INTELLIGENZ Pflicht-Test)
    # tries to prevent this upstream; this is the safety net.
    if not final_citations:
        annotated = re.sub(r"\s*\[\d+\]", "", annotated)

    yield {"type": "meta", "citations": final_citations, "content": annotated}
    yield {"type": "done"}


# Background tasks that outlive the HTTP request. Keyed by assistant
# message_id so /cancel can target a specific in-flight turn; the task is
# also held strongly here so the GC doesn't tear it down when the originating
# request finishes. Entries auto-removed via add_done_callback.
_BACKGROUND_TURNS: dict[str, asyncio.Task] = {}


async def _run_assistant_turn(
    *,
    chat: dict,
    text: str,
    chat_id: str,
    user_id: str,
    assistant_message_id: str,
) -> None:
    """Drive _assistant_turn_events as a detached task: write each yielded
    payload to chat_message_deltas (so any client subscribed via Realtime
    sees them), and finalize the assistant chat_messages row when the
    generator completes. Independent of the HTTP request that started it —
    closing the tab does not cancel this task."""
    seq = 0
    final_content = ""
    final_citations: list[dict] = []
    # Snapshot of every trace frame seen this turn, upserted by id so the
    # dispatch start/done pair collapses into one row (mirrors the frontend
    # applyDeltaToThread upsert). Persisted onto chat_messages.traces at
    # finalize so the activity collapsibles survive a hard reload.
    trace_index: dict[str, dict] = {}
    trace_order: list[str] = []
    try:
        async for payload in _assistant_turn_events(
            chat=chat, text=text, chat_id=chat_id, user_id=user_id,
        ):
            ptype = payload.get("type")
            if ptype == "meta":
                # Last writer wins: an error meta after partial deltas
                # overrides earlier metas (the generator only emits one).
                final_content = payload.get("content") or final_content
                final_citations = payload.get("citations") or []
            elif ptype == "trace":
                tid = payload.get("id")
                if tid:
                    if tid not in trace_index:
                        trace_order.append(tid)
                    trace_index[tid] = payload
            seq += 1
            try:
                await asyncio.to_thread(
                    _insert_delta,
                    message_id=assistant_message_id,
                    chat_id=chat_id,
                    user_id=user_id,
                    seq=seq,
                    payload=payload,
                )
            except Exception as ins_exc:  # noqa: BLE001
                # A delta-insert failure must not abort the rest of the
                # turn; the final UPDATE on chat_messages still carries the
                # complete answer. Log and continue.
                log.warning(
                    "delta insert failed (msg=%s seq=%s): %s",
                    assistant_message_id, seq, ins_exc,
                )
        final_traces = [trace_index[t] for t in trace_order] or None
        await asyncio.to_thread(
            _finalize_assistant_message,
            message_id=assistant_message_id,
            content=final_content,
            citations=final_citations,
            traces=final_traces,
            error=None,
        )
    except asyncio.CancelledError:
        # Process shutdown — try to mark the row so the frontend doesn't
        # spin forever if the user reloads after a backend restart.
        try:
            await asyncio.to_thread(
                _finalize_assistant_message,
                message_id=assistant_message_id,
                content=_friendly_gemini_error(RuntimeError("cancelled")),
                citations=[],
                traces=_terminate_inflight_traces(
                    trace_index, trace_order, "cancelled"
                ),
                error="cancelled",
            )
        except Exception:  # noqa: BLE001
            pass
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("assistant turn failed (msg=%s): %s", assistant_message_id, exc)
        try:
            await asyncio.to_thread(
                _finalize_assistant_message,
                message_id=assistant_message_id,
                content=final_content or _friendly_gemini_error(exc),
                citations=final_citations,
                traces=_terminate_inflight_traces(
                    trace_index, trace_order, type(exc).__name__
                ),
                error=f"{type(exc).__name__}: {str(exc)[:480]}",
            )
        except Exception:  # noqa: BLE001
            log.exception(
                "failed to finalize errored assistant msg %s", assistant_message_id
            )


def _terminate_inflight_traces(
    trace_index: dict[str, dict],
    trace_order: list[str],
    reason: str,
) -> list[dict] | None:
    """On cancel / error, walk the per-id trace snapshot and flip any frame
    still parked on kind='tool_call' to a synthetic terminated tool_response
    so the activity panel renders an interrupted row instead of a forever-
    spinner on reload."""
    if not trace_order:
        return None
    frames: list[dict] = []
    for tid in trace_order:
        frame = dict(trace_index[tid])
        if frame.get("kind") == "tool_call":
            frame["kind"] = "tool_response"
            frame["status"] = "error"
            frame["response"] = json.dumps(
                {"error": f"interrupted: {reason}"}, ensure_ascii=False
            )
        frames.append(frame)
    return frames


class SendMessageOut(BaseModel):
    user_message_id: str
    assistant_message_id: str


@router.post("/{chat_id}/messages", response_model=SendMessageOut)
async def send_message(
    chat_id: str, body: MessageIn, user_id: str = Depends(current_user_id)
):
    chat = _load_chat(chat_id, user_id)
    user_msg = await asyncio.to_thread(
        _persist_user_message, chat_id, user_id, body.text
    )
    asst_msg = await asyncio.to_thread(
        _create_streaming_assistant, chat_id, user_id
    )
    task = asyncio.create_task(
        _run_assistant_turn(
            chat=chat,
            text=body.text,
            chat_id=chat_id,
            user_id=user_id,
            assistant_message_id=asst_msg["id"],
        )
    )
    msg_id = asst_msg["id"]
    _BACKGROUND_TURNS[msg_id] = task
    task.add_done_callback(lambda _t: _BACKGROUND_TURNS.pop(msg_id, None))
    return SendMessageOut(
        user_message_id=user_msg["id"],
        assistant_message_id=msg_id,
    )


@router.post("/messages/{message_id}/cancel")
async def cancel_message(
    message_id: str, user_id: str = Depends(current_user_id)
):
    """User-initiated stop. Cancels the background turn (if still running);
    _run_assistant_turn's CancelledError handler finalizes the row to
    status='error' so the Realtime UPDATE drives the frontend cleanup. If
    the task already finished, the row is already terminal — no-op."""
    res = (
        supabase()
        .table("chat_messages")
        .select("id,user_id,status")
        .eq("id", message_id)
        .limit(1)
        .execute()
    )
    if not res.data or res.data[0]["user_id"] != user_id:
        raise HTTPException(404, "message not found")
    task = _BACKGROUND_TURNS.get(message_id)
    if task and not task.done():
        task.cancel()
    return {"cancelled": message_id}


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
