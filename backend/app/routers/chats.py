import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langsmith import traceable
from pydantic import BaseModel

log = logging.getLogger(__name__)


def _friendly_gemini_error(exc: Exception) -> str:
    """Vendor-neutral German message shown to the user when the upstream LLM
    fails. The technical detail (provider, HTTP code, error class) is logged
    via `log.warning` at the call site — never surfaced in the transcript."""
    return (
        "_⚠️ Die Antwort konnte gerade nicht erzeugt werden. "
        "Bitte in ein paar Sekunden erneut versuchen._"
    )

from app.auth import current_user_id
from app.config import settings
from app.db import supabase
from app.file_inventory import build_inventory_block
from app.gemini_client import gemini_client_untraced
from app.projektanalyse import (
    PROJEKTANALYSE_INSTRUCTIONS,
    PROJEKTANALYSE_TOOL,
    PROJEKTANALYSE_V2_TOOL,
    stream_projektanalyse,
    stream_projektanalyse_v2,
)
from app.retrieval import RetrievedChunk
from app.sufficiency import assess_sufficiency, build_continuation_hint
from app.tools import (
    LIST_DOCUMENT_OUTLINE_TOOL,
    READ_SECTION_TOOL,
    SEARCH_CHUNKS_TOOL,
    execute_search_chunks,
    list_document_outline_executor,
    read_section_executor,
)

router = APIRouter(prefix="/api/chats", tags=["chats"])

MAX_TOOL_ITERATIONS = 12

RETRIEVAL_TOOL_NAMES = {
    "search_chunks",
    "list_document_outline",
    "read_section",
}

CHAT_SYSTEM_PROMPT = (
    "Du bist ein technischer RAG-Assistent für Schweizer Bahn-/Ingenieur-"
    "projekt-Ausschreibungen. Du beantwortest Fragen ausschliesslich "
    "anhand der hochgeladenen Projektdokumente.\n\n"
    "VERHALTEN:\n"
    "• Sprache: Deutsch.\n"
    "• Tools: nutze die verfügbaren Retrieval-Tools, um Belege zu finden. "
    "Lies die Tool-Beschreibungen — sie sagen dir, wann welches Tool "
    "passt und wie sie sich abgrenzen. Bei Aggregations-Fragen (z.B. "
    "\"welche Bauherren\", \"alle Termine\") darfst du mehrere Tool-"
    "Aufrufe parallel emittieren.\n"
    "• Du formulierst Suchanfragen und Filter immer selbst aus der "
    "Frage des Nutzers. Du fragst NIEMALS den Nutzer nach Suchbegriffen, "
    "Synonymen, Quellen oder einer Suchanfrage zurück. Wenn ein Tool "
    "einen Fehler oder leere Treffer liefert, korrigiere den Aufruf "
    "selbst und versuch es erneut — niemals den Nutzer um eine Eingabe "
    "bitten.\n"
    "• Zitate: jeder belegte Satz/Aufzählungspunkt bekommt die `ref`-"
    "Nummer aus dem Tool-Ergebnis in eckigen Klammern, z.B. [1] oder "
    "[3]. Mehrere refs nacheinander sind ok ([1][3]). Refs aus allen "
    "Retrieval-Tools eines Turns sind fortlaufend durchnummeriert.\n"
    "• Scope-Fallback: wenn die Beschaffung nur SIA-Phasen 21 "
    "(Machbarkeit) und/oder 31 (Vorprojekt) umfasst und der Nutzer "
    "nach Bauprojekt (SIA 32/41) oder Ausführung (SIA 51+) fragt, "
    "antworte: \"Nicht Teil dieser Beschaffung — der Auftragsumfang "
    "umfasst nur [konkrete Phasen].\" Das ist KEIN 'nicht gefunden'-Fall.\n"
    "• Smalltalk und Meta-Fragen ('Hallo', 'wer bist du') ohne Tool-"
    "Aufruf kurz beantworten.\n\n"
    + PROJEKTANALYSE_INSTRUCTIONS
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


def _persist_error_message(
    *, chat_id: str, user_id: str, content: str, citations: list[dict] | None
) -> str | None:
    """Insert a best-effort assistant error row so the chat history reflects
    the failure. Returns the new message id or None if the insert itself
    failed (in which case the SSE 'done' frame just omits message_id)."""
    try:
        ins = (
            supabase()
            .table("chat_messages")
            .insert(
                {
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "role": "assistant",
                    "content": content,
                    "citations": citations,
                }
            )
            .execute()
        )
        return ins.data[0]["id"] if ins.data else None
    except Exception:
        log.exception("failed to persist error assistant message")
        return None


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


def _tool_name(tool_call) -> str | None:
    fn = getattr(tool_call, "function", None)
    return getattr(fn, "name", None) if fn else None


def _build_system_message(project_id: str, user_id: str) -> dict:
    inventory = build_inventory_block(project_id, user_id)
    if inventory:
        content = f"{CHAT_SYSTEM_PROMPT}\n\n---\n\n{inventory}"
    else:
        content = CHAT_SYSTEM_PROMPT
    return {"role": "system", "content": content}


def _citations_by_ref(chunks: list[RetrievedChunk]) -> list[dict]:
    """One citation per `ref` index. Order is significant: `chunks[i]` IS
    the chunk the model saw as `ref = i + 1` (set by execute_search_chunks
    via the `ref_offset` accumulator). DO NOT dedupe by chunk_id here —
    that would shift indices and break the `[N]` → citation mapping the
    frontend uses. Dedup by chunk_id happens at render time in the
    Message component, where we can dedupe + renumber atomically."""
    return [c.to_citation() for c in chunks]


@traceable(run_type="chain", name="chats.send_message")
async def _send_message_stream(
    *,
    chat: dict,
    text: str,
    chat_id: str,
    user_id: str,
    template: list[str] | None,
):
    """Multi-turn agent loop for a chat turn.

    Persists the user message, builds a system prompt with the project's
    file inventory, then loops up to MAX_TOOL_ITERATIONS times: each turn
    streams Gemini, collects any `search_chunks` tool calls, executes them,
    and feeds results back. Plain-text iterations stream deltas to the
    client; the citations meta frame is emitted *after* the answer text and
    contains only chunks the model actually retrieved.

    Hands off to projektanalyse v1/v2 if the model triggers their tool.
    """

    project_id = chat["project_id"]

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

    # 3. Build initial messages with the per-project file inventory.
    system_msg = await asyncio.to_thread(
        _build_system_message, project_id, user_id
    )
    messages: list[dict] = [system_msg, *history, {"role": "user", "content": text}]
    tools: list[dict] = [
        SEARCH_CHUNKS_TOOL,
        LIST_DOCUMENT_OUTLINE_TOOL,
        READ_SECTION_TOOL,
        PROJEKTANALYSE_TOOL,
        PROJEKTANALYSE_V2_TOOL,
    ]
    # When tool_choice="required" is set we restrict the bound tool list to
    # retrieval-only — otherwise Gemini happily satisfies the constraint by
    # picking run_projektanalyse_v2 (the cheapest path to "I called *a*
    # tool"), which is exactly the v2-fallback behavior the project rule
    # forbids. v1/v2 stay bound on all non-forced turns so the user-elected
    # path keeps working.
    retrieval_only_tools: list[dict] = [
        SEARCH_CHUNKS_TOOL,
        LIST_DOCUMENT_OUTLINE_TOOL,
        READ_SECTION_TOOL,
    ]

    collected_chunks: list[RetrievedChunk] = []
    ref_offset = 0
    parts: list[str] = []
    finished = False
    sufficiency_already_nudged = False  # one continuation hint per turn
    force_tool_next_iter = False  # plan 17.3 T1: bind sufficiency-fail to a tool call
    tool_error_streak = 0  # plan 17.3 T2: count consecutive all-error tool turns

    for iteration in range(MAX_TOOL_ITERATIONS):
        try:
            # Use the untraced client: the LangSmith OpenAI wrapper's
            # streaming reducer crashes on Gemini tool-call deltas whose
            # `index` field is None (TypeError: NoneType + int). Tracing
            # on the surrounding `chats.send_message` chain still captures
            # tool calls and embedding lookups — we just lose the inner
            # chat.completions span.
            # Plan 17.3 T1/T2: when sufficiency just nudged or the previous
            # turn returned only error envelopes, refuse to let the model
            # emit prose this turn. Gemini's OpenAI-compat shim honors
            # `tool_choice="required"`. The flag is consumed for one turn,
            # then cleared. We ALSO swap the bound tool list to retrieval-
            # only so the forced call can't satisfy the constraint by
            # invoking run_projektanalyse_v2 (the project rule forbids
            # auto-escalation to v2; v2 stays user-/model-elected on
            # non-forced turns only).
            forced_this_turn = force_tool_next_iter
            create_kwargs: dict = dict(
                model=settings.gemini_chat_model,
                messages=messages,
                tools=retrieval_only_tools if forced_this_turn else tools,
                stream=True,
            )
            if forced_this_turn:
                create_kwargs["tool_choice"] = "required"
                force_tool_next_iter = False
            stream = await asyncio.to_thread(
                lambda: gemini_client_untraced().chat.completions.create(
                    **create_kwargs
                )
            )
        except Exception as exc:
            # Gemini's OpenAI-compat shim returns generic 400 INVALID_ARGUMENT
            # without detail in `str(exc)`. Pull the response body off the
            # SDK's APIStatusError if available, plus a coarse payload shape
            # snapshot (message count by role, total chars) so we can debug
            # which round of tool calls produced the bad request.
            body = getattr(getattr(exc, "response", None), "text", None)
            shape = {
                "iteration": iteration,
                "msg_count": len(messages),
                "msg_chars": sum(
                    len(m.get("content") or "")
                    for m in messages
                    if isinstance(m.get("content"), str)
                ),
                "by_role": {
                    role: sum(1 for m in messages if m.get("role") == role)
                    for role in ("system", "user", "assistant", "tool")
                },
            }
            # On 400, dump the messages array (content truncated) so we can
            # see exactly which assistant/tool/history shape Gemini rejected.
            # Keep the role + content head + any tool-call ids per message.
            def _summarize_msg(m: dict) -> dict:
                role = m.get("role")
                summary: dict = {"role": role}
                c = m.get("content")
                if isinstance(c, str):
                    summary["content"] = c[:200] + ("…" if len(c) > 200 else "")
                else:
                    summary["content_type"] = type(c).__name__
                if m.get("tool_calls"):
                    summary["tool_calls"] = [
                        {
                            "id": tc.get("id"),
                            "name": tc.get("function", {}).get("name"),
                            "args_chars": len(
                                tc.get("function", {}).get("arguments") or ""
                            ),
                        }
                        for tc in m["tool_calls"]
                    ]
                if m.get("tool_call_id"):
                    summary["tool_call_id"] = m["tool_call_id"]
                return summary

            log.warning(
                "gemini chat.completions.create failed: %s | body=%s | shape=%s | messages=%s",
                exc,
                (body or "")[:600],
                shape,
                [_summarize_msg(m) for m in messages],
            )
            error_text = _friendly_gemini_error(exc)
            citations = _citations_by_ref(collected_chunks)
            msg_id = await asyncio.to_thread(
                _persist_error_message,
                chat_id=chat_id,
                user_id=user_id,
                content=error_text,
                citations=citations,
            )
            yield f"data: {json.dumps({'type': 'delta', 'content': error_text})}\n\n"
            yield f"data: {json.dumps({'type': 'meta', 'citations': citations})}\n\n"
            done_payload: dict = {"type": "done"}
            if msg_id:
                done_payload["message_id"] = msg_id
            yield f"data: {json.dumps(done_payload)}\n\n"
            return

        is_final_iteration = iteration == MAX_TOOL_ITERATIONS - 1
        iter_text_parts: list[str] = []
        pending_tool_calls: dict[int, dict] = {}
        projektanalyse_triggered: str | None = None
        stream_error: Exception | None = None

        try:
            for event in stream:
                if not event.choices:
                    continue
                choice = event.choices[0]
                delta = choice.delta
                content_delta = getattr(delta, "content", None)
                if content_delta:
                    iter_text_parts.append(content_delta)
                    # Stream to client only on the final iteration (no more
                    # tool calls expected). Earlier iterations may interleave
                    # text + tool calls; we replay text after we know the
                    # turn ended without tool calls below.
                tool_calls = getattr(delta, "tool_calls", None) or []
                for tc in tool_calls:
                    idx = getattr(tc, "index", 0) or 0
                    slot = pending_tool_calls.setdefault(
                        idx,
                        {
                            "id": None,
                            "name": None,
                            "arguments": "",
                        },
                    )
                    if getattr(tc, "id", None):
                        slot["id"] = tc.id
                    fn = getattr(tc, "function", None)
                    if fn:
                        if getattr(fn, "name", None):
                            slot["name"] = fn.name
                        args_chunk = getattr(fn, "arguments", None)
                        if args_chunk:
                            slot["arguments"] += args_chunk
                    name = slot.get("name")
                    if name == "run_projektanalyse":
                        projektanalyse_triggered = "v1"
                    elif name == "run_projektanalyse_v2":
                        projektanalyse_triggered = "v2"
                if projektanalyse_triggered:
                    break
        except Exception as exc:
            stream_error = exc
            log.warning("gemini stream interrupted: %s", exc)
        finally:
            try:
                await asyncio.to_thread(stream.close)
            except Exception:
                pass

        # Projektanalyse handoff — same as before, fully owns the rest of
        # the SSE stream.
        if projektanalyse_triggered == "v1":
            async for sse in stream_projektanalyse(
                template=template, chat_id=chat_id, user_id=user_id
            ):
                yield sse
            return
        if projektanalyse_triggered == "v2":
            async for sse in stream_projektanalyse_v2(
                template=template, chat_id=chat_id, user_id=user_id
            ):
                yield sse
            return

        if stream_error is not None:
            notice = _friendly_gemini_error(stream_error)
            citations = _citations_by_ref(collected_chunks)
            assistant_text = "".join(parts) + "".join(iter_text_parts)
            tail = f"\n\n{notice}" if assistant_text.strip() else notice
            yield f"data: {json.dumps({'type': 'delta', 'content': assistant_text + tail if not parts else tail})}\n\n"
            assistant_text += tail
            msg_id = await asyncio.to_thread(
                _persist_error_message,
                chat_id=chat_id,
                user_id=user_id,
                content=assistant_text.strip(),
                citations=citations,
            )
            yield f"data: {json.dumps({'type': 'meta', 'citations': citations})}\n\n"
            done_payload = {"type": "done"}
            if msg_id:
                done_payload["message_id"] = msg_id
            yield f"data: {json.dumps(done_payload)}\n\n"
            return

        # No retrieval tool calls → final answer (or sufficiency-nudged
        # continuation, see below).
        retrieval_calls = [
            slot
            for slot in pending_tool_calls.values()
            if slot.get("name") in RETRIEVAL_TOOL_NAMES
        ]
        if not retrieval_calls:
            assistant_text = "".join(iter_text_parts)

            # Sufficiency check (Reasoning Agent / SCA pattern): before we
            # let the model finalize, ask another Gemini call whether the
            # collected chunks are enough to answer. If not, append the
            # rater's feedback as a system message and let the loop run one
            # more iteration. One nudge per turn — never block the answer.
            iterations_remaining = MAX_TOOL_ITERATIONS - iteration - 1
            if (
                collected_chunks
                and not sufficiency_already_nudged
                and iterations_remaining > 0
            ):
                verdict = await asyncio.to_thread(
                    assess_sufficiency,
                    question=text,
                    chunks=collected_chunks,
                )
                if not verdict["sufficient"]:
                    sufficiency_already_nudged = True
                    # Plan 17.3 T1: bind the next iteration to a tool call.
                    # The plain "system message + continue" pattern (plan
                    # 17.2) was advisory — UAT showed the model regularly
                    # ignored it and emitted prose anyway. Forcing
                    # tool_choice="required" on the next create() call
                    # makes the contract enforceable rather than persuasive.
                    force_tool_next_iter = True
                    # Reflect the model's would-be-final text into the
                    # message history (so the next iteration sees what it
                    # was about to say) and append the continuation hint.
                    if assistant_text:
                        messages.append(
                            {"role": "assistant", "content": assistant_text}
                        )
                    messages.append(
                        {
                            "role": "system",
                            "content": build_continuation_hint(verdict),
                        }
                    )
                    continue  # re-enter the loop for one more retrieval round

            if assistant_text:
                yield f"data: {json.dumps({'type': 'delta', 'content': assistant_text})}\n\n"
                parts.append(assistant_text)
            finished = True
            break

        # Tool-call iteration: dispatch each retrieval tool by name, append
        # assistant message with tool_calls, append tool messages with
        # results, loop. Don't stream any text from this iteration — the
        # model will produce the final text in a later iteration after
        # seeing tool results. All three tools share the `ref_offset`
        # accumulator so citations stay contiguous across the turn.
        assistant_tool_calls = []
        tool_messages = []
        # Plan 17.3 T2: track structured-error envelopes per turn. If every
        # call this turn errored, we replace the tool messages with one
        # synthetic system directive so the model can't read the error
        # `guidance` field and surface it to the user as prose (which is
        # what UAT showed Q4 doing on the empty-`query` envelope).
        turn_errors: list[dict] = []
        turn_call_count = 0
        for slot in retrieval_calls:
            tool_name = slot.get("name") or ""
            try:
                args = json.loads(slot.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}

            if tool_name == "search_chunks":
                executor = execute_search_chunks
            elif tool_name == "list_document_outline":
                executor = list_document_outline_executor
            elif tool_name == "read_section":
                executor = read_section_executor
            else:
                # Defensive: should not happen since RETRIEVAL_TOOL_NAMES
                # gates membership, but keep the loop robust.
                continue

            result = await asyncio.to_thread(
                executor,
                args=args,
                project_id=project_id,
                user_id=user_id,
                ref_offset=ref_offset,
            )
            chunks_added: list[RetrievedChunk] = result.pop("_chunks", []) or []
            collected_chunks.extend(chunks_added)
            ref_offset += len(result.get("results", []))

            turn_call_count += 1
            err = result.get("error") if isinstance(result, dict) else None
            if isinstance(err, dict):
                turn_errors.append({"tool": tool_name, **err})

            tc_id = slot.get("id") or f"call_{iteration}_{len(assistant_tool_calls)}"
            # Replay the parsed args we actually executed, not the raw stream
            # buffer. Gemini's compat shim validates JSON inside `arguments`
            # and rejects truncated/invalid streams from previous iterations.
            assistant_tool_calls.append(
                {
                    "id": tc_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(args, ensure_ascii=False),
                    },
                }
            )
            tool_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

        # Carry forward any text the model emitted alongside the tool call.
        # (Gemini sometimes emits a brief "Ich suche das nach…" before the
        # call. Keep it server-side as a thinking note; don't stream it.)
        thinking = "".join(iter_text_parts)
        # Gemini's OpenAI-compat shim rejects {"content": null, "tool_calls":
        # [...]} with 400 INVALID_ARGUMENT once such messages start piling up
        # in a multi-round tool conversation. OpenAI accepts both null and "";
        # Gemini accepts "" only. Always send a string.
        messages.append(
            {
                "role": "assistant",
                "content": thinking,
                "tool_calls": assistant_tool_calls,
            }
        )

        # Plan 17.3 T2: when every tool call this turn errored, the JSON
        # envelopes still get fed back to the model so the call/response
        # pairs stay balanced (Gemini rejects assistant.tool_calls without
        # matching role:tool messages). But we ALSO append a directive
        # system message instructing the model to retry, and force a tool
        # call on the next iteration via tool_choice="required". This
        # prevents the failure mode UAT exposed (model reads the error
        # `guidance` field, paraphrases it as "Bitte geben Sie an…", and
        # streams that to the user).
        all_errored = turn_call_count > 0 and len(turn_errors) == turn_call_count
        if all_errored:
            tool_error_streak += 1
            first_err = turn_errors[0]
            retry_directive = (
                "TOOL-RETRY: dein letzter Tool-Aufruf "
                f"({first_err.get('tool', '?')}) hatte einen Fehler "
                f"({first_err.get('code', 'unknown')}"
                + (
                    f", argument={first_err['argument']}"
                    if first_err.get("argument")
                    else ""
                )
                + "). Korrigiere die Argumente selbst aus der Frage "
                "des Nutzers und rufe das Tool sofort erneut auf. "
                "Antworte NICHT mit Prosa und frage NIEMALS den Nutzer "
                "nach Eingaben — du musst das Tool jetzt erneut "
                "aufrufen."
            )
            messages.extend(tool_messages)
            messages.append({"role": "system", "content": retry_directive})
            force_tool_next_iter = True
            # Hard cap: if the model keeps producing broken tool calls
            # despite force-tool, stop burning iterations and surface the
            # generic warning at the end of the loop. Without v2 fallback
            # (per project rule) this is the safe terminal state.
            if tool_error_streak >= 3:
                warn_text = (
                    "_⚠️ Die Frage konnte mit den verfügbaren Tools "
                    "nicht beantwortet werden. Bitte anders "
                    "formulieren._"
                )
                yield f"data: {json.dumps({'type': 'delta', 'content': warn_text})}\n\n"
                parts.append(warn_text)
                finished = True
                break
        else:
            tool_error_streak = 0
            messages.extend(tool_messages)

        if is_final_iteration:
            # Loop cap reached without a final text answer. Tell the user.
            warn_text = (
                "_⚠️ Es konnte trotz mehrerer Versuche keine Antwort zusammen-"
                "gestellt werden. Bitte Frage anders formulieren._"
            )
            yield f"data: {json.dumps({'type': 'delta', 'content': warn_text})}\n\n"
            parts.append(warn_text)
            finished = True
            break

    # Emit the citations meta frame *after* the answer text and persist the
    # assistant message.
    citations = _citations_by_ref(collected_chunks)
    yield f"data: {json.dumps({'type': 'meta', 'citations': citations})}\n\n"

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
    _ = finished  # silence unused var; loop control already complete


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
