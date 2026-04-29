"""Smoke test: create a corpus, upload a file, query, delete.

Plan 18.1 T7. Run once after 18.1 to confirm GCP plumbing works end-to-end
before any application code touches Vertex RAG. The script is fully
self-contained: it creates a temporary corpus, uploads the fixture PDF,
imports it, runs one retrieval query, and deletes everything it created.

Exits 0 on success. Any GCP / IAM / quota error surfaces as the natural
exception with a non-zero exit code.

Usage:
    GCP_PROJECT_ID=... GCP_LOCATION=... GCS_FILES_BUCKET=... \\
        GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json \\
        backend/venv/bin/python scripts/smoke_vertex_rag.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import vertexai
from google.cloud import storage
from vertexai.preview import rag

PROJECT_ID = os.environ["GCP_PROJECT_ID"]
LOCATION = os.environ["GCP_LOCATION"]
BUCKET = os.environ["GCS_FILES_BUCKET"]

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "smoke_sample.pdf"
GCS_KEY = "_smoke/sample.pdf"
GCS_URI = f"gs://{BUCKET}/{GCS_KEY}"


def main() -> int:
    if not FIXTURE.exists():
        print(f"ERROR: fixture missing at {FIXTURE}", file=sys.stderr)
        return 2

    vertexai.init(project=PROJECT_ID, location=LOCATION)
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(BUCKET)
    blob = bucket.blob(GCS_KEY)

    corpus = None
    try:
        # 1. Upload fixture to GCS
        blob.upload_from_filename(str(FIXTURE))
        print(f"Uploaded {FIXTURE.name} -> {GCS_URI}")

        # 2. Create a temporary corpus with the configured embedding model
        corpus = rag.create_corpus(
            display_name="smoke-test",
            backend_config=rag.RagVectorDbConfig(
                rag_embedding_model_config=rag.RagEmbeddingModelConfig(
                    vertex_prediction_endpoint=rag.VertexPredictionEndpoint(
                        publisher_model=(
                            "publishers/google/models/"
                            + os.environ.get(
                                "VERTEX_RAG_EMBEDDING_MODEL",
                                "text-embedding-005",
                            )
                        )
                    )
                )
            ),
        )
        print(f"Created corpus: {corpus.name}")

        # 3. Import the file
        op = rag.import_files(corpus.name, [GCS_URI])
        print(f"Import LRO: {getattr(op, 'name', op)}")

        # 4. Wait for ingestion (poll up to 5 minutes)
        deadline = time.time() + 300
        while time.time() < deadline:
            files = list(rag.list_files(corpus.name))
            if files and all(f.file_status.state.name == "ACTIVE" for f in files):
                print(f"All {len(files)} file(s) active")
                break
            print(f"Waiting on ingestion ({len(files)} file(s) seen)...")
            time.sleep(15)
        else:
            raise TimeoutError("Ingestion did not complete in 5 minutes")

        # 5. Retrieval query
        result = rag.retrieval_query(
            rag_resources=[rag.RagResource(rag_corpus=corpus.name)],
            text="What is this document about?",
            rag_retrieval_config=rag.RagRetrievalConfig(top_k=3),
        )
        contexts = result.contexts.contexts
        print(f"Got {len(contexts)} contexts")
        for ctx in contexts:
            score = getattr(ctx, "score", None)
            score_str = f"score={score:.3f} " if score is not None else ""
            print(f"  - {score_str}text={ctx.text[:80]!r}")

        if not contexts:
            raise RuntimeError("Retrieval returned 0 contexts")

    finally:
        # 6. Cleanup — idempotent: tolerate missing resources on rerun
        if corpus is not None:
            try:
                rag.delete_corpus(corpus.name)
                print(f"Deleted corpus {corpus.name}")
            except Exception as exc:  # noqa: BLE001
                print(f"WARN: corpus delete failed: {exc}", file=sys.stderr)
        try:
            blob.delete()
            print(f"Deleted {GCS_URI}")
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: blob delete failed: {exc}", file=sys.stderr)

    print("Smoke test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
