"""Empirical validation for plan 19.1.

Two MODES against the live Suedi-Areal corpus:

  Mode A — RAW Gemini with native vertex_rag_store grounding (Agent Builder
  pattern, NO rag_specialist instruction). Shows what the model does on its
  own at different similarity_top_k settings and whether batched-vs-fanned-out
  matters at the raw layer.

  Mode B — REAL rag_specialist via ADK Runner (full SIA instruction including
  ROLLEN-FRAGEN). Shows whether the production prompt already handles the
  singular-role case without any plan changes.

Scores each run against ground-truth facts the user verified are in the corpus
(4 TP-Leiter names + Bausumme 39'114'000).

Run:
    backend/venv/bin/python backend/scripts/test_batched_rag_recall.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path

# Load .env BEFORE importing backend modules so settings reads it.
from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.rag_corpus import _init_vertex_for  # noqa: E402

from google import genai  # noqa: E402
from google.genai import types as gt  # noqa: E402
from google.oauth2 import service_account  # noqa: E402


def _scoped_credentials():
    """Service-account creds scoped for Vertex/genai access."""
    if not settings.gcp_service_account_json_path:
        return None
    return service_account.Credentials.from_service_account_file(
        settings.gcp_service_account_json_path,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )


CORPUS_ID = "7221275711883968512"
# user-provided corpus name (display): sleek-rag-b2748283-5b1d-4474-a3cd-31cbdbe9dc56
# resource path follows Agent-Builder snippet pattern: us-central1
CORPUS = f"projects/{settings.gcp_project_id}/locations/us-central1/ragCorpora/{CORPUS_ID}"

SINGLE_Q = "Wer ist der Projektleiter?"

BATCH_Qs = [
    "In welcher Phase werden Ingenieurdienstleistungen angefragt?",
    "Welche Bauherren sind beteiligt?",
    "Wie heisst der Projektleiter?",
    "Welche Termine sind vorgesehen? Gibt es zwingende Meilensteine fuer z.B. Zwischentermine, Gleisschlagwochenenden oder aehnliche?",
    "Was ist die Bausumme?",
    "Welche Drittprojekte tangieren den Perimeter?",
    "Welche Rahmenbedingungen betreffen das Projekt hinsichtlich Termine, Bauzeit oder aehnlichem?",
    "Welche Elemente sind vom Bauprojekt zu ueberarbeiten? Wie viel Stunden sind dafuer in der Ausschreibung vorgesehen?",
    "Welche Elemente sind im Ausfuehrungsprojekt zu ueberabreiten oder zu aendern?",
    "Ist die Vermessung Bestandteil unseres Auftrags oder ist diese nur zu koordinieren?",
    "Steht in den Plaenen irgendwo der Kommentar 'Ist in einer spaeteren Phase zu Detaillieren.' oder etwas aehnliches?",
]

GROUND_TRUTH_NAMES = ["Pascal Ryser", "Thomas Kieliger", "Silvia Bucher", "Luca Nosetti"]
GROUND_TRUTH_BAUSUMME = re.compile(r"39[\.\s']?114[\.\s']?000|39[,.]114|39\s*Mio")


def _client() -> genai.Client:
    """Build a Vertex genai client using the backend's service account."""
    _init_vertex_for(CORPUS)
    creds = _scoped_credentials()
    return genai.Client(
        vertexai=True,
        project=settings.gcp_project_id,
        location="us-central1",
        credentials=creds,
    )


def _make_tool(top_k: int) -> gt.Tool:
    return gt.Tool(
        retrieval=gt.Retrieval(
            vertex_rag_store=gt.VertexRagStore(
                rag_resources=[gt.VertexRagStoreRagResource(rag_corpus=CORPUS)],
                similarity_top_k=top_k,
            )
        )
    )


def _config(top_k: int) -> gt.GenerateContentConfig:
    return gt.GenerateContentConfig(
        temperature=1,
        top_p=1,
        max_output_tokens=65535,
        tools=[_make_tool(top_k)],
        thinking_config=gt.ThinkingConfig(thinking_budget=-1),
    )


def _ask_sync(client: genai.Client, prompt: str, top_k: int) -> tuple[str, int]:
    """Single synchronous call. Returns (text, n_grounding_chunks)."""
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[gt.Content(role="user", parts=[gt.Part.from_text(text=prompt)])],
        config=_config(top_k),
    )
    text_parts: list[str] = []
    n_chunks = 0
    for cand in resp.candidates or []:
        if cand.content and cand.content.parts:
            for p in cand.content.parts:
                if p.text and not getattr(p, "thought", False):
                    text_parts.append(p.text)
        gm = getattr(cand, "grounding_metadata", None)
        if gm and gm.grounding_chunks:
            n_chunks += len(gm.grounding_chunks)
    return ("\n".join(text_parts).strip(), n_chunks)


async def _ask_async(client: genai.Client, prompt: str, top_k: int) -> tuple[str, int]:
    """Async wrapper around the sync call (genai sync client is blocking)."""
    return await asyncio.to_thread(_ask_sync, client, prompt, top_k)


# ---------------------------------------------------------------------------
# Mode B: ask via the real rag_specialist agent (full ADK Runner + instruction)
# ---------------------------------------------------------------------------


async def _ask_rag_specialist(prompt: str) -> tuple[str, int]:
    """Run a single question through make_rag_specialist via ADK Runner.

    Uses the production instruction (RAG_SPECIALIST_INSTRUCTION) so the
    ROLLEN-FRAGEN rule etc. are in effect. Returns (text, n_grounding_chunks)
    just like Mode A.
    """
    from google.adk.runners import Runner
    from google.adk.sessions.in_memory_session_service import InMemorySessionService
    from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
    from app.adk.agents import make_rag_specialist

    agent = make_rag_specialist(CORPUS)
    runner = Runner(
        app_name="rag_specialist_test",
        agent=agent,
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
    )
    session = await runner.session_service.create_session(
        app_name="rag_specialist_test",
        user_id="test_user",
    )
    content = gt.Content(role="user", parts=[gt.Part.from_text(text=prompt)])
    last_text = ""
    n_chunks = 0
    async for event in runner.run_async(
        user_id=session.user_id,
        session_id=session.id,
        new_message=content,
    ):
        if event.content and event.content.parts:
            text = "\n".join(
                p.text for p in event.content.parts
                if p.text and not getattr(p, "thought", False)
            )
            if text:
                last_text = text
        gm = getattr(event, "grounding_metadata", None)
        if gm and getattr(gm, "grounding_chunks", None):
            n_chunks += len(gm.grounding_chunks)
    await runner.close()
    return (last_text.strip(), n_chunks)


def _score_names(text: str) -> dict:
    return {n: (n.lower() in text.lower()) for n in GROUND_TRUTH_NAMES}


def _score_bausumme(text: str) -> bool:
    return bool(GROUND_TRUTH_BAUSUMME.search(text))


def _print_section(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def run_single_question_tests(client: genai.Client) -> None:
    """Test 1+2: single Projektleiter question at top_k=10 vs 20."""
    for top_k in (10, 20):
        _print_section(f"TEST: single question, similarity_top_k={top_k}")
        t0 = time.perf_counter()
        text, n_chunks = _ask_sync(client, SINGLE_Q, top_k)
        dt = time.perf_counter() - t0
        names = _score_names(text)
        print(f"  latency: {dt:.2f}s   grounding_chunks: {n_chunks}")
        print(f"  names found: {sum(names.values())}/4   {names}")
        print(f"  --- response ---")
        print(text[:1200])
        if len(text) > 1200:
            print(f"  ... ({len(text) - 1200} chars truncated)")


def run_batch_test(client: genai.Client) -> None:
    """Test 3: 11 questions concatenated into ONE generate_content call."""
    _print_section("TEST: 11 questions BATCHED into one call (top_k=20)")
    numbered = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(BATCH_Qs))
    prompt = (
        "Beantworte die folgenden Fragen. Strukturiere die Antwort als "
        "'Frage X: ...\\nAntwort X: ...' pro Frage:\n\n" + numbered
    )
    t0 = time.perf_counter()
    text, n_chunks = _ask_sync(client, prompt, top_k=20)
    dt = time.perf_counter() - t0
    names = _score_names(text)
    bausumme = _score_bausumme(text)
    print(f"  latency: {dt:.2f}s   grounding_chunks: {n_chunks}")
    print(f"  names found: {sum(names.values())}/4   {names}")
    print(f"  Bausumme 39'114'000 found: {bausumme}")
    print(f"  --- response (first 2000 chars) ---")
    print(text[:2000])
    if len(text) > 2000:
        print(f"  ... ({len(text) - 2000} chars truncated)")


async def run_fanout_test(client: genai.Client) -> None:
    """Test 4: 11 questions FANNED OUT via asyncio.gather (parallel)."""
    _print_section("TEST: 11 questions FANNED OUT (parallel, top_k=20)")
    t0 = time.perf_counter()
    results = await asyncio.gather(
        *[_ask_async(client, q, top_k=20) for q in BATCH_Qs]
    )
    dt = time.perf_counter() - t0
    combined_text = "\n".join(r[0] for r in results)
    total_chunks = sum(r[1] for r in results)
    names = _score_names(combined_text)
    bausumme = _score_bausumme(combined_text)
    print(f"  wallclock: {dt:.2f}s   total grounding_chunks: {total_chunks}")
    print(f"  names found across all answers: {sum(names.values())}/4   {names}")
    print(f"  Bausumme 39'114'000 found: {bausumme}")
    print(f"  --- per-question summary ---")
    for i, (q, (text, n)) in enumerate(zip(BATCH_Qs, results), start=1):
        first_line = (text.split("\n", 1)[0] if text else "<empty>").strip()
        print(f"  Q{i:>2} chunks={n:>2} | {first_line[:140]}")


async def run_mode_b_single_question() -> None:
    """Mode B test 1: 'Wer ist der Projektleiter?' through real rag_specialist.

    This is THE test for whether the production prompt fixes the singular-role
    case without any plan changes. If 4/4, drop T3' (prompt fix) — instruction
    already works. If 1/4, we need to tighten ROLLEN-FRAGEN.
    """
    _print_section("MODE B: single question via REAL rag_specialist (ADK Runner)")
    t0 = time.perf_counter()
    text, n_chunks = await _ask_rag_specialist(SINGLE_Q)
    dt = time.perf_counter() - t0
    names = _score_names(text)
    print(f"  latency: {dt:.2f}s   grounding_chunks: {n_chunks}")
    print(f"  names found: {sum(names.values())}/4   {names}")
    print(f"  --- response ---")
    print(text[:1500])
    if len(text) > 1500:
        print(f"  ... ({len(text) - 1500} chars truncated)")


async def run_mode_b_fanout() -> None:
    """Mode B test 2: 11 questions fanned out, each through real rag_specialist.

    This is the apples-to-apples comparison with Mode A's fan-out: same
    parallelism, but each sub-call uses the production rag_specialist
    (instruction + top_k=10 from agents.py) instead of raw Gemini.
    """
    _print_section("MODE B: 11 questions FANNED OUT via REAL rag_specialist")
    t0 = time.perf_counter()
    results = await asyncio.gather(
        *[_ask_rag_specialist(q) for q in BATCH_Qs],
        return_exceptions=True,
    )
    dt = time.perf_counter() - t0
    texts = []
    total_chunks = 0
    for r in results:
        if isinstance(r, BaseException):
            texts.append(f"<ERROR: {r!r}>")
            continue
        txt, n = r
        texts.append(txt)
        total_chunks += n
    combined = "\n".join(texts)
    names = _score_names(combined)
    bausumme = _score_bausumme(combined)
    print(f"  wallclock: {dt:.2f}s   total grounding_chunks: {total_chunks}")
    print(f"  names found across all answers: {sum(names.values())}/4   {names}")
    print(f"  Bausumme 39'114'000 found: {bausumme}")
    print(f"  --- per-question summary ---")
    for i, (q, t) in enumerate(zip(BATCH_Qs, texts), start=1):
        first_line = (t.split("\n", 1)[0] if t else "<empty>").strip()
        print(f"  Q{i:>2} | {first_line[:140]}")


async def main() -> None:
    print(f"corpus: {CORPUS}")
    print(f"project: {settings.gcp_project_id}   location: us-central1")
    client = _client()

    # Mode A — raw Gemini with native grounding (Agent Builder pattern)
    run_single_question_tests(client)
    run_batch_test(client)
    await run_fanout_test(client)

    # Mode B — real rag_specialist via ADK Runner (production instruction)
    await run_mode_b_single_question()
    await run_mode_b_fanout()


if __name__ == "__main__":
    asyncio.run(main())
