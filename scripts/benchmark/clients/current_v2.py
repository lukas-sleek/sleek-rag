"""Variant C: Current sleek-rag projektanalyse v2 (full-corpus per question).

Triggers v2 by sending "Projektanalyse v2 erstellen" with the question
list as `projektanalyse_template` in the MessageIn body. v2 streams
progress events while it answers each question in parallel, then emits
ONE big delta containing the full markdown report. We split that report
back into per-question slices by the `## {i}. {question}` headers
emitted by `_assemble_report`.
"""

import asyncio
import json
import os
import re
import time

import httpx

from scripts.benchmark.clients.current_chat import (
    _api_base,
    create_chat,
    login,
)

V2_TRIGGER = "Projektanalyse v2 erstellen"


async def ask_v2(
    token: str, chat_id: str, questions: list[dict], timeout_s: int = 1200
) -> dict:
    template = [q["question"] for q in questions]
    answer_parts: list[str] = []
    citations: list[dict] = []
    progress_events: list[dict] = []
    t0 = time.monotonic()

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        async with client.stream(
            "POST",
            f"{_api_base()}/api/chats/{chat_id}/messages",
            json={"text": V2_TRIGGER, "projektanalyse_template": template},
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                raw = line[len("data: "):]
                if raw.strip() == "[DONE]":
                    break
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if "progress" in payload:
                    progress_events.append(payload["progress"])
                    continue
                t = payload.get("type")
                if t == "delta" and payload.get("content"):
                    answer_parts.append(payload["content"])
                elif t == "meta" and payload.get("citations") is not None:
                    citations = payload["citations"]
                elif t == "done":
                    break

    return {
        "report": "".join(answer_parts),
        "citations": citations,
        "progress_events": progress_events,
        "latency_s": round(time.monotonic() - t0, 2),
    }


def _split_report(report: str, questions: list[dict]) -> list[dict]:
    """Split the v2 report into per-question slices.

    `_assemble_report` emits sections shaped like `## {i}. {question}\\n\\n{answer}`.
    We anchor on `^## N. ` headers (numbered, possibly trailed by other
    `##` headers in the body of an answer)."""
    headers = list(re.finditer(r"^## (\d+)\. .*$", report, flags=re.MULTILINE))
    slices: dict[int, str] = {}
    for j, m in enumerate(headers):
        idx = int(m.group(1)) - 1
        start = m.end()
        end = headers[j + 1].start() if j + 1 < len(headers) else len(report)
        slices[idx] = report[start:end].strip()

    out: list[dict] = []
    for i, q in enumerate(questions):
        out.append(
            {
                "id": q["id"],
                "question": q["question"],
                "answer": slices.get(i, ""),
            }
        )
    return out


async def run_question_set(questions: list[dict]) -> list[dict]:
    project_id = os.environ["BENCHMARK_PROJECT_ID"]
    token = await login()
    chat_id = await create_chat(token, project_id)
    bundle = await ask_v2(token, chat_id, questions)

    per_q = _split_report(bundle["report"], questions)
    # Attach the same citations + per-run latency to each slice — v2
    # produces a single report so we can't attribute timing per question.
    for entry in per_q:
        entry["citations"] = bundle["citations"]
        entry["latency_s_total"] = bundle["latency_s"]
    return per_q
