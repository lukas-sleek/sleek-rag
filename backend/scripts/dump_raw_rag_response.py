"""One-shot raw dump of a vertex_rag_store grounded response.

Mirrors how rag_specialist queries (see backend/app/adk/agents.py:144-154):
  Tool(retrieval=Retrieval(vertex_rag_store=VertexRagStore(
      rag_resources=[RagResource(rag_corpus=...)], similarity_top_k=N)))
fed to gemini-2.5-flash via genai.Client.models.generate_content. No filtering,
no interpretation — just dumps the entire pydantic response as JSON so the
caller can inspect every field by eye.

Run:
    backend/venv/bin/python backend/scripts/dump_raw_rag_response.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.rag_corpus import _init_vertex_for  # noqa: E402

from google import genai  # noqa: E402
from google.genai import types as gt  # noqa: E402
from google.oauth2 import service_account  # noqa: E402


# Same corpus the user named: sleek-rag-b2748283-5b1d-4474-a3cd-31cbdbe9dc56
CORPUS_ID = "7221275711883968512"
CORPUS = f"projects/{settings.gcp_project_id}/locations/us-central1/ragCorpora/{CORPUS_ID}"

QUERY = "Wer ist der projektleiter"

# Match rag_specialist's similarity_top_k (agents.py: _RETRIEVAL_TOP_K).
TOP_K = 10


def _scoped_credentials():
    if not settings.gcp_service_account_json_path:
        return None
    return service_account.Credentials.from_service_account_file(
        settings.gcp_service_account_json_path,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )


def main() -> None:
    _init_vertex_for(CORPUS)
    client = genai.Client(
        vertexai=True,
        project=settings.gcp_project_id,
        location="us-central1",
        credentials=_scoped_credentials(),
    )

    tool = gt.Tool(
        retrieval=gt.Retrieval(
            vertex_rag_store=gt.VertexRagStore(
                rag_resources=[gt.VertexRagStoreRagResource(rag_corpus=CORPUS)],
                similarity_top_k=TOP_K,
            )
        )
    )
    cfg = gt.GenerateContentConfig(
        temperature=1,
        top_p=1,
        max_output_tokens=8192,
        tools=[tool],
    )

    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[gt.Content(role="user", parts=[gt.Part.from_text(text=QUERY)])],
        config=cfg,
    )

    print(f"# corpus: {CORPUS}")
    print(f"# query: {QUERY!r}")
    print(f"# top_k: {TOP_K}")
    print()
    # Full raw pydantic dump — no exclude_none, no truncation, every field.
    payload = resp.model_dump(exclude_none=False, exclude_unset=False, mode="json")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
