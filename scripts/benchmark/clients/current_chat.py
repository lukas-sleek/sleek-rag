"""Variant B: Current sleek-rag chat (custom loop, pgvector retrieval).

Drives the running FastAPI backend end-to-end as a logged-in user:
login via Supabase password grant, create a fresh chat in the target
project, then POST each question to /api/chats/{id}/messages and parse
the SSE stream (delta + meta + done frames).
"""

import asyncio
import json
import os
import time

import httpx


def _env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None or val == "":
        raise RuntimeError(f"missing env var: {name}")
    return val


def _api_base() -> str:
    return os.environ.get("BENCHMARK_API_BASE", "http://localhost:8000")


def _supabase_url() -> str:
    return _env("SUPABASE_URL")


def _supabase_anon_key() -> str:
    # Backend env naming: NEXT_PUBLIC_SUPABASE_ANON_KEY (frontend) is the
    # public anon key. SUPABASE_SERVICE_ROLE_KEY is server-only. Auth
    # password grant works with the anon key.
    key = (
        os.environ.get("SUPABASE_ANON_KEY")
        or os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    )
    if not key:
        raise RuntimeError(
            "set SUPABASE_ANON_KEY or NEXT_PUBLIC_SUPABASE_ANON_KEY"
        )
    return key


async def login() -> str:
    email = os.environ.get("BENCHMARK_TEST_EMAIL", "test@test.com")
    password = os.environ.get("BENCHMARK_TEST_PASSWORD", "12345678")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{_supabase_url()}/auth/v1/token?grant_type=password",
            json={"email": email, "password": password},
            headers={"apikey": _supabase_anon_key()},
        )
        r.raise_for_status()
        return r.json()["access_token"]


async def create_chat(token: str, project_id: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{_api_base()}/api/chats",
            json={"project_id": project_id, "title": f"benchmark-{int(time.time())}"},
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        return r.json()["id"]


async def ask(token: str, chat_id: str, question: str, timeout_s: int = 300) -> dict:
    answer_parts: list[str] = []
    citations: list[dict] = []
    t0 = time.monotonic()

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        async with client.stream(
            "POST",
            f"{_api_base()}/api/chats/{chat_id}/messages",
            json={"text": question},
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
                t = payload.get("type")
                if t == "delta" and payload.get("content"):
                    answer_parts.append(payload["content"])
                elif t == "meta" and payload.get("citations") is not None:
                    citations = payload["citations"]
                elif t == "done":
                    break

    return {
        "answer": "".join(answer_parts),
        "citations": citations,
        "latency_s": round(time.monotonic() - t0, 2),
    }


async def run_question_set(questions: list[dict]) -> list[dict]:
    project_id = _env("BENCHMARK_PROJECT_ID")
    token = await login()
    chat_id = await create_chat(token, project_id)
    results: list[dict] = []
    for q in questions:
        r = await ask(token, chat_id, q["question"])
        results.append({"id": q["id"], "question": q["question"], **r})
    return results


if __name__ == "__main__":  # one-question dry run
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from scripts.benchmark.env_loader import load_env

    load_env()
    qs = [{"id": "smoke", "question": "Wie heisst der Projektleiter?"}]
    out = asyncio.run(run_question_set(qs))
    print(json.dumps(out, ensure_ascii=False, indent=2)[:1500])
