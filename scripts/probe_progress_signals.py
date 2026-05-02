"""Probe what signals Vertex emits during/after a folder-import LRO.

Captures, against the existing corpus 3638662208310738944 in us-central1:

  - rag_file shape: name, display_name, gcs_source.uris, file_status.state
  - chunk → file_id mapping: numeric file_id from retrieval_query matches
    the trailing segment of rag_file.name
  - whether `gcs_source` survives in serverless (so we can match imported
    files back to GCS paths even though source_uri at retrieval time is a
    Vertex temp bucket)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import vertexai  # noqa: E402
from google.cloud.aiplatform_v1beta1.services.vertex_rag_data_service import (  # noqa: E402
    VertexRagDataServiceClient,
)
from google.cloud.aiplatform_v1beta1.types import ListRagFilesRequest  # noqa: E402
from google.oauth2 import service_account  # noqa: E402
from vertexai.preview import rag  # noqa: E402

from app.config import settings  # noqa: E402

LOCATION = "us-central1"
CORPUS_ID = "3638662208310738944"


def main() -> int:
    creds = service_account.Credentials.from_service_account_file(
        settings.gcp_service_account_json_path
    )
    vertexai.init(project=settings.gcp_project_id, location=LOCATION, credentials=creds)
    corpus_name = (
        f"projects/{settings.gcp_project_id}/locations/{LOCATION}/ragCorpora/{CORPUS_ID}"
    )

    # 1. SDK wrapper view
    print("=== rag.list_files (SDK wrapper) ===")
    files = list(rag.list_files(corpus_name))
    for f in files:
        gs_uris = list(f.gcs_source.uris) if f.gcs_source else []
        print(f"  - rag_file_name = {f.name}")
        print(f"    display_name  = {f.display_name!r}")
        print(f"    state         = {f.file_status.state.name}")
        print(f"    gcs_source    = {gs_uris}")
        print(f"    direct_upload = {bool(getattr(f, 'direct_upload_source', None))}")

    # 2. GAPIC view — vertexai's list_files wrapper sometimes drops fields
    print("\n=== GAPIC list_rag_files ===")
    gapic = VertexRagDataServiceClient(
        credentials=creds,
        client_options={"api_endpoint": f"{LOCATION}-aiplatform.googleapis.com"},
    )
    pager = gapic.list_rag_files(ListRagFilesRequest(parent=corpus_name))
    for f in pager:
        gs_uris = list(f.gcs_source.uris) if f.gcs_source else []
        print(f"  - {f.name.split('/')[-1]}: display={f.display_name!r} "
              f"state={f.file_status.state.name} uris={gs_uris}")

    # 3. Confirm chunk.file_id ↔ rag_file.name mapping
    print("\n=== chunk.file_id ↔ rag_file.name ===")
    res = rag.retrieval_query(
        rag_resources=[rag.RagResource(rag_corpus=corpus_name)],
        text="Wie heisst der Projektleiter?",
        rag_retrieval_config=rag.RagRetrievalConfig(top_k=3),
    )
    rag_files_by_id = {f.name.rsplit("/", 1)[-1]: f for f in files}
    for ctx in res.contexts.contexts:
        chunk_file_id = str(ctx.chunk.file_id) if ctx.chunk else None
        match = rag_files_by_id.get(chunk_file_id) if chunk_file_id else None
        print(f"  chunk.file_id={chunk_file_id} -> "
              f"display_name={match.display_name if match else 'NO MATCH'!r}")

    # 4. importedRagFilesCount / failedRagFilesCount / skippedRagFilesCount —
    #    just dump the response shape from a rag.import_files call against the
    #    same folder. (Skips by default if files already imported.)
    bucket = "sleek-rag-files-dev"
    folder_uri = f"gs://{bucket}/84747b27-a193-452f-a200-74e5a83feaee/09997640-d19d-4b17-a0fd-9b3190b78fc3/"
    print(f"\n=== rag.import_files (re-import same folder for shape capture) ===")
    print(f"folder: {folder_uri}")
    try:
        resp = rag.import_files(corpus_name, paths=[folder_uri])
        # Synchronous wrapper returns ImportRagFilesResponse-ish object.
        print("response repr:", repr(resp)[:400])
        for attr in (
            "imported_rag_files_count", "failed_rag_files_count",
            "skipped_rag_files_count", "partial_failures",
        ):
            print(f"  {attr}: {getattr(resp, attr, '<missing>')!r}")
    except Exception as exc:
        print(f"import_files raised: {type(exc).__name__}: {str(exc)[:300]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
