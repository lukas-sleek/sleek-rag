"""LLM-judge: scores each variant's per-question answer with Gemini 2.5 Pro.

Each (question, variant_label, answer) triple is scored on four
dimensions (0-3 each):

  - accuracy:     facts correct against the source documents
  - completeness: answer-relevant content captured
  - citation:     citations present, specific (page-level), correct
  - conciseness:  appropriately scoped, no waffle

Source PDFs (optional) are uploaded once via the Files API and reused
across all judgments. Set BENCHMARK_JUDGE_SOURCE_DIR to enable; without
it the judge falls back to its prior knowledge of the answer.
"""

import os
import time
from pathlib import Path
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field


class DimensionScore(BaseModel):
    accuracy: int = Field(ge=0, le=3)
    completeness: int = Field(ge=0, le=3)
    citation: int = Field(ge=0, le=3)
    conciseness: int = Field(ge=0, le=3)


class JudgeVerdict(BaseModel):
    scores: DimensionScore
    rationale: str


class PairVerdict(BaseModel):
    winner: Literal["A", "B", "tie"]
    rationale: str


JUDGE_MODEL = os.environ.get("BENCHMARK_JUDGE_MODEL", "gemini-2.5-pro")


def _make_client() -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=os.environ["GCP_PROJECT_ID"],
        location=os.environ.get("GCP_LOCATION", "europe-west3"),
    )


def upload_sources(client: genai.Client, source_dir: str | None) -> list:
    """Upload all PDFs in source_dir via the Files API. Returns Part list
    suitable to prepend to every judgment prompt. Empty list if no dir.
    """
    if not source_dir:
        return []
    root = Path(source_dir)
    if not root.exists():
        raise RuntimeError(f"BENCHMARK_JUDGE_SOURCE_DIR does not exist: {source_dir}")
    parts = []
    for pdf in sorted(root.rglob("*.pdf")):
        uploaded = client.files.upload(
            file=str(pdf),
            config=types.UploadFileConfig(mime_type="application/pdf"),
        )
        parts.append(
            types.Part.from_uri(file_uri=uploaded.uri, mime_type=uploaded.mime_type)
        )
    return parts


JUDGE_SYSTEM = """Du bist ein strenger, fachkundiger Bewerter für RAG-Antworten zum Schweizer Bauwesen (SIA-Phasen, Submissionen, Schweizer Bahn).

Bewerte JEDE Antwort separat auf vier Dimensionen, jede 0–3:

- **accuracy** (0–3): Sind die Fakten korrekt belegbar? 0=falsch/halluziniert, 1=teilweise falsch, 2=überwiegend korrekt mit kleinen Fehlern, 3=vollständig korrekt.
- **completeness** (0–3): Wie viel der für die Frage relevanten Information ist enthalten? 0=fehlt fast alles, 1=Teilantwort, 2=hauptsächlich vollständig, 3=alles Wesentliche.
- **citation** (0–3): Sind Quellen genannt und spezifisch (Datei + Seite/Kapitel)? 0=keine Belege, 1=vage Belege, 2=spezifisch aber unvollständig, 3=spezifisch und vollständig.
- **conciseness** (0–3): Ist die Antwort angemessen knapp? 0=lange Abschweifungen, 1=zu lang, 2=fast richtig, 3=knapp und auf den Punkt.

Begründe in 2–3 Sätzen.
"""


def judge_answer(
    client: genai.Client,
    source_parts: list,
    question: str,
    variant_label: str,
    answer: str,
) -> JudgeVerdict:
    prompt = (
        f"Frage: {question}\n\n"
        f"Antwort von Variante {variant_label}:\n```\n{answer}\n```\n\n"
        "Bewerte diese Antwort auf den vier Dimensionen und gib eine kurze Begründung."
    )
    contents = list(source_parts) + [types.Part.from_text(text=prompt)]

    cfg = types.GenerateContentConfig(
        temperature=0.0,
        max_output_tokens=4096,
        system_instruction=JUDGE_SYSTEM,
        response_mime_type="application/json",
        response_schema=JudgeVerdict,
        thinking_config=types.ThinkingConfig(thinking_level="LOW"),
        safety_settings=[
            types.SafetySetting(category=c, threshold="OFF")
            for c in [
                "HARM_CATEGORY_HATE_SPEECH",
                "HARM_CATEGORY_DANGEROUS_CONTENT",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "HARM_CATEGORY_HARASSMENT",
            ]
        ],
    )
    resp = client.models.generate_content(
        model=JUDGE_MODEL, contents=contents, config=cfg
    )
    return JudgeVerdict.model_validate_json(resp.text)


def judge_results(
    per_question: list[dict],
    source_dir: str | None = None,
) -> list[dict]:
    """per_question: list of {id, question, variants: {A: {answer,...}, B: ..., C: ...}}.

    Returns a list of {id, question, judgments: {A: JudgeVerdict, ...}}."""
    client = _make_client()
    source_parts = upload_sources(client, source_dir)

    out: list[dict] = []
    for entry in per_question:
        judgments: dict[str, dict] = {}
        for label, payload in entry["variants"].items():
            answer = payload.get("answer", "") or ""
            t0 = time.monotonic()
            verdict = judge_answer(
                client, source_parts, entry["question"], label, answer
            )
            judgments[label] = {
                **verdict.model_dump(),
                "_judge_latency_s": round(time.monotonic() - t0, 2),
            }
        out.append(
            {
                "id": entry["id"],
                "question": entry["question"],
                "judgments": judgments,
            }
        )
    return out


def winner_for(judgments: dict[str, dict]) -> str:
    """Pick the variant with the highest total score; 'tie' if multiple."""
    totals: dict[str, int] = {}
    for label, j in judgments.items():
        s = j["scores"]
        totals[label] = (
            s["accuracy"] + s["completeness"] + s["citation"] + s["conciseness"]
        )
    if not totals:
        return "tie"
    top = max(totals.values())
    leaders = [label for label, total in totals.items() if total == top]
    return leaders[0] if len(leaders) == 1 else "tie"
