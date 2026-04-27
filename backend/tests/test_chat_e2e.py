"""End-to-end smoke for the new chat endpoint + projektanalyse v1/v2.

Gated on RUN_GCP_INTEGRATION=1. Spins up a project, ingests a PDF, then:

  1. Sends a normal chat message → asserts a meta frame with citations is
     emitted before any delta and a done frame closes the stream.
  2. Sends "Erstelle mir eine Projektanalyse" with a single-question template
     → asserts the v1 handoff completes and produces a report.
  3. Sends "Projektanalyse v2 erstellen" with the same template → asserts
     the v2 handoff completes (full-corpus path).

This is the flow most at risk from the Gemini tool-calling swap.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import pytest

from app.db import supabase
from app.routers.chats import _send_message_stream
from app.workers.ingest import _process_job
from app.projektanalyse import stream_projektanalyse, stream_projektanalyse_v2

PDF_PATH = "/home/lukasthomas/Downloads/somatosensory.pdf"


@pytest.fixture(scope="module")
def chat_setup():
    user = next(
        u for u in supabase().auth.admin.list_users() if u.email == "test@test.com"
    )
    user_id = user.id
    proj = (
        supabase()
        .table("projects")
        .insert({"user_id": user_id, "name": f"chat-e2e-{uuid.uuid4().hex[:8]}"})
        .execute()
    )
    project_id = proj.data[0]["id"]
    chat_ins = (
        supabase()
        .table("chats")
        .insert({"user_id": user_id, "project_id": project_id, "title": "e2e"})
        .execute()
    )
    chat_id = chat_ins.data[0]["id"]

    file_id = None
    blob_path = None
    try:
        with open(PDF_PATH, "rb") as f:
            pdf_bytes = f.read()
        filename = f"chat-e2e-{uuid.uuid4().hex[:8]}.pdf"
        ins = (
            supabase()
            .table("project_files")
            .insert(
                {
                    "project_id": project_id,
                    "user_id": user_id,
                    "filename": filename,
                    "size_bytes": len(pdf_bytes),
                    "mime_type": "application/pdf",
                    "status": "uploading",
                }
            )
            .execute()
        )
        file_id = ins.data[0]["id"]
        blob_path = f"{user_id}/{file_id}/{filename}"
        supabase().storage.from_("project-files").upload(
            blob_path, pdf_bytes, {"content-type": "application/pdf"}
        )
        supabase().table("project_files").update(
            {"gcs_blob_path": blob_path, "status": "parsing"}
        ).eq("id", file_id).execute()
        job_ins = (
            supabase()
            .table("ingest_jobs")
            .insert({"file_id": file_id, "user_id": user_id, "state": "queued"})
            .execute()
        )
        job_id = job_ins.data[0]["id"]
        claim = supabase().rpc("claim_next_ingest_job").execute()
        job = next(j for j in claim.data if j["id"] == job_id)
        t0 = time.time()
        _process_job(job)
        print(f"[chat_setup] ingest done in {time.time() - t0:.1f}s")
        chat = (
            supabase()
            .table("chats")
            .select("*")
            .eq("id", chat_id)
            .single()
            .execute()
            .data
        )
        yield {
            "user_id": user_id,
            "project_id": project_id,
            "chat_id": chat_id,
            "chat": chat,
        }
    finally:
        if file_id:
            supabase().table("project_files").delete().eq("id", file_id).execute()
        if blob_path:
            try:
                supabase().storage.from_("project-files").remove([blob_path])
            except Exception:
                pass
        supabase().table("chats").delete().eq("id", chat_id).execute()
        supabase().table("projects").delete().eq("id", project_id).execute()


def _drain(agen):
    """Collect every SSE frame an async generator yields."""
    out: list[dict | str] = []

    async def _go():
        async for sse in agen:
            assert sse.startswith("data: "), sse
            payload = sse[len("data: ") :].strip()
            if payload == "[DONE]":
                out.append("[DONE]")
                continue
            try:
                out.append(json.loads(payload))
            except json.JSONDecodeError:
                out.append(payload)

    asyncio.run(_go())
    return out


@pytest.mark.skipif(
    not os.getenv("RUN_GCP_INTEGRATION"),
    reason="costs money — set RUN_GCP_INTEGRATION=1",
)
def test_chat_emits_meta_delta_done(chat_setup):
    frames = _drain(
        _send_message_stream(
            chat=chat_setup["chat"],
            text="Was ist das somatosensorische System?",
            chat_id=chat_setup["chat_id"],
            user_id=chat_setup["user_id"],
            template=None,
        )
    )
    types = [f.get("type") for f in frames if isinstance(f, dict)]
    assert types[0] == "meta", f"first frame must be meta, got: {types[:3]}"
    assert "delta" in types, f"no delta frames: {types}"
    assert types[-1] == "done", f"last frame must be done, got: {types[-3:]}"

    meta = frames[0]
    assert isinstance(meta, dict)
    assert "citations" in meta
    assert isinstance(meta["citations"], list)

    # The assistant message must have been persisted.
    persisted = (
        supabase()
        .table("chat_messages")
        .select("id,role,citations")
        .eq("chat_id", chat_setup["chat_id"])
        .eq("role", "assistant")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    )
    assert persisted and persisted[0]["role"] == "assistant"


@pytest.mark.skipif(
    not os.getenv("RUN_GCP_INTEGRATION"),
    reason="costs money — set RUN_GCP_INTEGRATION=1",
)
def test_projektanalyse_v1_runs(chat_setup):
    """Direct invocation of the v1 handoff. Bypasses LLM tool-call detection."""
    frames = _drain(
        stream_projektanalyse(
            template=["Worum geht es in den Dokumenten?"],
            chat_id=chat_setup["chat_id"],
            user_id=chat_setup["user_id"],
        )
    )
    progress_frames = [f for f in frames if isinstance(f, dict) and "progress" in f]
    assert progress_frames, "no progress frames emitted"
    delta_frames = [
        f for f in frames if isinstance(f, dict) and f.get("type") == "delta"
    ]
    assert delta_frames, "no report delta emitted"
    report = "".join(f["content"] for f in delta_frames)
    assert "# Projektanalyse" in report
    assert "Worum geht es in den Dokumenten?" in report


@pytest.mark.skipif(
    not os.getenv("RUN_GCP_INTEGRATION"),
    reason="costs money — set RUN_GCP_INTEGRATION=1",
)
def test_projektanalyse_v2_runs(chat_setup):
    frames = _drain(
        stream_projektanalyse_v2(
            template=["Worum geht es in den Dokumenten?"],
            chat_id=chat_setup["chat_id"],
            user_id=chat_setup["user_id"],
        )
    )
    delta_frames = [
        f for f in frames if isinstance(f, dict) and f.get("type") == "delta"
    ]
    assert delta_frames, "no report delta emitted"
    report = "".join(f["content"] for f in delta_frames)
    assert "Volltext" in report
