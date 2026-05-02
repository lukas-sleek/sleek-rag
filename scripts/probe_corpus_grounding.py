"""One-shot research probe: query an existing Vertex RAG corpus and dump
the *exact* shape returned by both `rag.retrieval_query` and a Gemini
generate_content call grounded on the corpus, so we can see what
metadata (uri, page span, ...) we still get.

Run:
    backend/venv/bin/python scripts/probe_corpus_grounding.py 3638662208310738944
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import vertexai  # noqa: E402
from google.oauth2 import service_account  # noqa: E402
from vertexai.preview import rag  # noqa: E402

from app.config import settings  # noqa: E402


def _proto_to_dict(msg) -> dict:
    from google.protobuf.json_format import MessageToDict
    try:
        return MessageToDict(msg._pb if hasattr(msg, "_pb") else msg, preserving_proto_field_name=False)
    except Exception:
        return {"_repr": repr(msg)[:300]}


def main(corpus_id: str) -> int:
    creds = service_account.Credentials.from_service_account_file(
        settings.gcp_service_account_json_path
    )
    location = sys.argv[2] if len(sys.argv) > 2 else "us-central1"
    vertexai.init(
        project=settings.gcp_project_id,
        location=location,
        credentials=creds,
    )

    corpus_name = (
        f"projects/{settings.gcp_project_id}"
        f"/locations/{location}"
        f"/ragCorpora/{corpus_id}"
    )
    print(f"Probing corpus: {corpus_name}\n")

    # 1. List files to capture per-file metadata shape (display_name, uri, ...)
    try:
        files = list(rag.list_files(corpus_name))
        print(f"=== rag.list_files: {len(files)} file(s) ===")
        for f in files[:3]:
            print(f"  - name={f.name}")
            print(f"    display_name={getattr(f, 'display_name', None)!r}")
            print(f"    file_status={getattr(f, 'file_status', None)!r}")
            print(f"    direct dir: {sorted(d for d in dir(f) if not d.startswith('_'))[:30]}")
        print()
    except Exception as e:
        print(f"list_files failed: {e}\n")

    # 2. retrieval_query — the canonical retrieval entry point
    print("=== rag.retrieval_query ===")
    result = rag.retrieval_query(
        rag_resources=[rag.RagResource(rag_corpus=corpus_name)],
        text="Wie heisst der Projektleiter?",
        rag_retrieval_config=rag.RagRetrievalConfig(top_k=5),
    )
    contexts = result.contexts.contexts
    print(f"context count: {len(contexts)}")
    for i, ctx in enumerate(contexts):
        print(f"\n--- ctx[{i}] ---")
        print(f"  fields: {sorted(d for d in dir(ctx) if not d.startswith('_'))}")
        for attr in (
            "score", "source_uri", "source_display_name",
            "page_span", "chunk", "text",
        ):
            v = getattr(ctx, attr, "<missing>")
            if isinstance(v, str) and len(v) > 200:
                v = v[:200] + "..."
            print(f"  {attr}: {v!r}")
    print()

    # 3. Raw proto dump of the first context for the absolute ground truth
    if contexts:
        print("=== first context as dict (proto MessageToDict) ===")
        try:
            print(json.dumps(_proto_to_dict(contexts[0]), indent=2, ensure_ascii=False)[:2500])
        except Exception as e:
            print(f"proto dump failed: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "3638662208310738944"))
