"""Smoke test the new serverless RAG path end-to-end.

Creates a temporary corpus in us-central1 with RagManagedVertexVectorSearch,
imports the existing test PDFs from gs://sleek-rag-files-us-dev/_smoke/,
polls list_rag_files until at least one file is ACTIVE, runs a retrieval
query and prints the grounding shape, then deletes the corpus.

Run: backend/venv/bin/python scripts/smoke_serverless_corpus.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import rag_corpus  # noqa: E402
from app.gcs import storage_client  # noqa: E402
from vertexai.preview import rag  # noqa: E402

LOCATION = "us-central1"
BUCKET = "sleek-rag-files-us-dev"
SMOKE_PREFIX = "_smoke/"


def _seed_fixture() -> str:
    """Upload the project's smoke fixture PDF into the bucket if missing."""
    fixture = Path(__file__).resolve().parent / "fixtures" / "smoke_sample.pdf"
    if not fixture.exists():
        raise SystemExit(f"fixture missing at {fixture}")
    blob = storage_client().bucket(BUCKET).blob(SMOKE_PREFIX + "sample.pdf")
    if not blob.exists():
        blob.upload_from_filename(str(fixture))
        print(f"Uploaded fixture -> gs://{BUCKET}/{SMOKE_PREFIX}sample.pdf")
    else:
        print(f"Fixture already present at gs://{BUCKET}/{SMOKE_PREFIX}sample.pdf")
    return f"gs://{BUCKET}/{SMOKE_PREFIX}"


def main() -> int:
    folder_uri = _seed_fixture()
    rag_corpus._init_vertex_at(LOCATION)

    corpus = rag.create_corpus(
        display_name="smoke-serverless",
        backend_config=rag_corpus._vector_db_config(),
    )
    print(f"Created serverless corpus: {corpus.name}")

    try:
        op = rag.import_files_async.__wrapped__ if hasattr(rag.import_files_async, "__wrapped__") else None
        # Use the same code path the app uses.
        import asyncio
        op_name = asyncio.run(rag_corpus.import_folder(corpus.name, folder_uri))
        print(f"Import LRO: {op_name}")

        deadline = time.time() + 600  # serverless ingest can take minutes
        while time.time() < deadline:
            files = list(rag.list_files(corpus.name))
            states = [f.file_status.state.name for f in files]
            print(f"  list_rag_files: {len(files)} file(s), states={states}")
            if files and any(s == "ACTIVE" for s in states):
                break
            time.sleep(15)
        else:
            raise TimeoutError("no file became ACTIVE within 10 min")

        print("\n=== retrieval_query ===")
        result = rag.retrieval_query(
            rag_resources=[rag.RagResource(rag_corpus=corpus.name)],
            text="What is this document about?",
            rag_retrieval_config=rag.RagRetrievalConfig(top_k=3),
        )
        contexts = result.contexts.contexts
        if not contexts:
            raise SystemExit("retrieval returned 0 contexts — smoke FAILED")
        for i, ctx in enumerate(contexts):
            print(f"  ctx[{i}]: file_id={ctx.chunk.file_id} "
                  f"display_name={getattr(ctx, 'source_display_name', '')!r} "
                  f"text={ctx.text[:60]!r}")
    finally:
        try:
            rag_corpus.delete_corpus(corpus.name)
            print(f"\nDeleted corpus {corpus.name}")
        except Exception as exc:
            print(f"WARN: corpus delete failed: {exc}")

    print("\nSmoke test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
