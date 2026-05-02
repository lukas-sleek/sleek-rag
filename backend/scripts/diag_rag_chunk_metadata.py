"""Empirical probe: does the serverless RAG corpus emit page_span / page metadata?

Background
----------
Plan 20.0 migrated us from the legacy RagManagedDb (EU) to serverless mode
backed by RagManagedVertexVectorSearch (Vector Search 2.0, us-central1).
Observation: in the chat UI, citations no longer carry page numbers, even
though `streaming_agent_tool._append_grounding_chunks` reads
`rag_chunk.page_span.{first_page,last_page}` and the SDK schema still has
those fields. Public docs do not state explicitly whether serverless
populates them.

This script does NOT change anything. It only:

  Mode `inspect`  (default)
    - hits the existing corpus via two paths and dumps EVERY field it can
      pull off each returned chunk, so we can see whether `page_span` is
      None, missing, or populated:

        path 1: rag.retrieval_query(...)
                → RetrieveContextsResponse.contexts.contexts[*]
                  fields: source_uri, source_display_name, text, score,
                          chunk (with page_span)

        path 2: genai.models.generate_content(..., tools=[vertex_rag_store])
                → resp.candidates[0].grounding_metadata.grounding_chunks[*]
                  fields: retrieved_context.{uri,title,text,rag_chunk.page_span}

  Mode `reingest <gs://bucket/file.pdf>`
    - creates a throwaway test corpus
    - imports the same PDF under several ingest-config variants
    - retrieves once per variant
    - prints which variant produced page_span values
    - cleans the corpus up
    Variants tried:
      A) Layout Parser, no transformation_config (current production)
      B) Layout Parser + ChunkingConfig(1024,200)
      C) LLM Parser (gemini-2.5-flash) instead of layout parser
      D) Layout Parser, no chunking, but request v1beta1 directly so we see
         whether the v1beta1 surface returns more fields than v1

Usage
-----
    backend/venv/bin/python backend/scripts/diag_rag_chunk_metadata.py inspect
    backend/venv/bin/python backend/scripts/diag_rag_chunk_metadata.py reingest \
        gs://your-bucket/some-known-multi-page.pdf
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.rag_corpus import (  # noqa: E402
    _credentials,
    _init_vertex_at,
    _init_vertex_for,
    _layout_parser_config,
    _vector_db_config,
)

from google import genai  # noqa: E402
from google.genai import types as gt  # noqa: E402
from google.oauth2 import service_account  # noqa: E402
from vertexai.preview import rag  # noqa: E402


# Existing serverless corpus we are debugging.
CORPUS_ID = "7221275711883968512"
CORPUS = f"projects/{settings.gcp_project_id}/locations/us-central1/ragCorpora/{CORPUS_ID}"

PROBE_QUERY = "Wer ist der Projektleiter?"


# ---------------------------------------------------------------------------
# Generic introspection helpers
# ---------------------------------------------------------------------------

_PROTO_NOISE = {
    "DESCRIPTOR", "Extensions", "ListFields", "FromString", "MergeFrom",
    "ParseFromString", "SerializeToString", "WhichOneof", "ByteSize",
    "Clear", "ClearField", "CopyFrom", "DiscardUnknownFields",
    "FindInitializationErrors", "HasField", "IsInitialized",
    "MergeFromString", "RegisterExtension", "SerializePartialToString",
    "SetInParent", "UnknownFields",
    "model_config", "model_fields", "model_computed_fields",
    "model_extra", "model_fields_set", "model_dump", "model_dump_json",
    "model_copy", "model_construct", "model_json_schema", "model_post_init",
    "model_rebuild", "model_validate", "model_validate_json",
    "model_validate_strings", "model_parametrized_name", "Config",
    "construct", "copy", "dict", "from_orm", "json", "parse_file",
    "parse_obj", "parse_raw", "schema", "schema_json", "update_forward_refs",
    "validate",
}


def _attrs(obj: Any) -> dict[str, Any]:
    """Return a {name: value} dict for every public attribute of `obj`.

    Tries hard to see proto fields (no __dict__) and pydantic fields.
    Skips dunder names, callables, and known noise/methods.
    """
    if obj is None:
        return {}
    candidates: list[str] = []

    pb = getattr(obj, "_pb", None)
    if pb is not None:
        try:
            for f in pb.DESCRIPTOR.fields:
                candidates.append(f.name)
        except Exception:
            pass
    if not candidates:
        d = getattr(obj, "DESCRIPTOR", None)
        if d is not None and hasattr(d, "fields"):
            try:
                candidates = [f.name for f in d.fields]
            except Exception:
                pass
    if not candidates:
        names = getattr(obj, "__dict__", None)
        if names:
            candidates = list(names.keys())
    if not candidates:
        candidates = [n for n in dir(obj) if not n.startswith("_")]

    out: dict[str, Any] = {}
    for n in candidates:
        if n.startswith("_") or n in _PROTO_NOISE:
            continue
        try:
            v = getattr(obj, n)
        except Exception as e:
            v = f"<err:{e!r}>"
        if callable(v):
            continue
        out[n] = v
    return out


def _shorten(v: Any, n: int = 120) -> str:
    s = repr(v)
    return s if len(s) <= n else s[: n - 3] + "..."


def _dump(label: str, obj: Any, depth: int = 0, max_depth: int = 3) -> None:
    """Recursive pretty-dump of attributes up to `max_depth`."""
    pad = "  " * depth
    if obj is None:
        print(f"{pad}{label}: None")
        return
    primitives = (str, int, float, bool, bytes)
    if isinstance(obj, primitives):
        print(f"{pad}{label}: {_shorten(obj)}")
        return
    if isinstance(obj, (list, tuple)):
        print(f"{pad}{label}: <{type(obj).__name__} len={len(obj)}>")
        for i, item in enumerate(obj[:3]):
            _dump(f"[{i}]", item, depth + 1, max_depth)
        if len(obj) > 3:
            print(f"{pad}  ... (+{len(obj) - 3} more)")
        return
    print(f"{pad}{label}: <{type(obj).__name__}>")
    if depth >= max_depth:
        return
    for k, v in _attrs(obj).items():
        _dump(k, v, depth + 1, max_depth)


def _section(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


# ---------------------------------------------------------------------------
# Path 1: direct retrieve_contexts (no LLM in the loop)
# ---------------------------------------------------------------------------

def probe_retrieve_contexts(corpus: str, query: str = PROBE_QUERY) -> None:
    """Hit the corpus with rag.retrieval_query and dump every chunk verbatim.

    This is the lowest-level public surface — no Gemini, no grounding
    pipeline. If page_span is missing here, it is the corpus / backend
    not populating it, full stop.
    """
    _section(f"PATH 1 — rag.retrieval_query   corpus={corpus}")
    _init_vertex_for(corpus)
    cfg = rag.RagRetrievalConfig(top_k=5)
    resp = rag.retrieval_query(
        text=query,
        rag_resources=[rag.RagResource(rag_corpus=corpus)],
        rag_retrieval_config=cfg,
    )
    print(f"response type: {type(resp).__name__}")
    print(f"top-level attrs: {sorted(_attrs(resp).keys())}")

    contexts = getattr(getattr(resp, "contexts", None), "contexts", None) or []
    print(f"got {len(contexts)} contexts")
    if not contexts:
        return

    print()
    print("Per-chunk field map (presence + value):")
    print("-" * 78)
    for i, c in enumerate(contexts):
        print(f"\n  chunk #{i}: type={type(c).__name__}")
        flat = _attrs(c)
        for k in sorted(flat):
            v = flat[k]
            # `chunk` (RagChunk) is the field that should carry page_span
            if k in ("chunk", "rag_chunk"):
                print(f"    {k}: <{type(v).__name__}>")
                for kk, vv in _attrs(v).items():
                    if kk == "page_span":
                        ps_attrs = _attrs(vv) if vv is not None else {}
                        print(
                            f"      page_span: {vv!r}  "
                            f"first_page={ps_attrs.get('first_page')!r}  "
                            f"last_page={ps_attrs.get('last_page')!r}"
                        )
                    else:
                        print(f"      {kk}: {_shorten(vv)}")
            else:
                print(f"    {k}: {_shorten(v)}")


# ---------------------------------------------------------------------------
# Path 2: grounded generate_content via genai (production path mirror)
# ---------------------------------------------------------------------------

def _scoped_credentials():
    if not settings.gcp_service_account_json_path:
        return None
    return service_account.Credentials.from_service_account_file(
        settings.gcp_service_account_json_path,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )


def probe_grounded_generate(corpus: str, query: str = PROBE_QUERY) -> None:
    """Match the production grounding path and dump grounding_chunks verbatim.

    This is what `rag_specialist` ultimately runs. If page_span is missing
    here but present in path 1, the loss happens in the grounding pipeline.
    """
    _section(f"PATH 2 — genai grounded generate_content   corpus={corpus}")
    _init_vertex_for(corpus)
    client = genai.Client(
        vertexai=True,
        project=settings.gcp_project_id,
        location="us-central1",
        credentials=_scoped_credentials(),
    )
    tool = gt.Tool(
        retrieval=gt.Retrieval(
            vertex_rag_store=gt.VertexRagStore(
                rag_resources=[gt.VertexRagStoreRagResource(rag_corpus=corpus)],
                similarity_top_k=5,
            )
        )
    )
    cfg = gt.GenerateContentConfig(
        temperature=1, top_p=1, max_output_tokens=4096, tools=[tool]
    )
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[gt.Content(role="user", parts=[gt.Part.from_text(text=query)])],
        config=cfg,
    )
    cands = resp.candidates or []
    if not cands:
        print("no candidates returned")
        return
    gm = getattr(cands[0], "grounding_metadata", None)
    if gm is None:
        print("no grounding_metadata on first candidate")
        return

    print(f"grounding_metadata top-level attrs: {sorted(_attrs(gm).keys())}")
    chunks = getattr(gm, "grounding_chunks", None) or []
    print(f"grounding_chunks: {len(chunks)}")
    for i, c in enumerate(chunks):
        print(f"\n  chunk #{i}: type={type(c).__name__}")
        rc = getattr(c, "retrieved_context", None)
        if rc is None:
            print("    retrieved_context: None")
            continue
        rc_attrs = _attrs(rc)
        for k in sorted(rc_attrs):
            v = rc_attrs[k]
            if k == "rag_chunk":
                print(f"    rag_chunk: <{type(v).__name__}>")
                for kk, vv in _attrs(v).items():
                    if kk == "page_span":
                        ps = _attrs(vv) if vv is not None else {}
                        print(
                            f"      page_span: {vv!r}  "
                            f"first_page={ps.get('first_page')!r}  "
                            f"last_page={ps.get('last_page')!r}"
                        )
                    else:
                        print(f"      {kk}: {_shorten(vv)}")
            else:
                print(f"    {k}: {_shorten(v)}")


# ---------------------------------------------------------------------------
# Reingest experiment: try a fresh corpus with each ingest-config variant
# ---------------------------------------------------------------------------

def _llm_parser_cfg() -> rag.LlmParserConfig:
    return rag.LlmParserConfig(
        model_name=(
            f"projects/{settings.gcp_project_id}"
            f"/locations/{settings.gcp_location}"
            f"/publishers/google/models/gemini-2.5-flash"
        ),
        max_parsing_requests_per_min=60,
    )


def _make_corpus(display_suffix: str) -> str:
    _init_vertex_at(settings.gcp_location)
    corpus = rag.create_corpus(
        display_name=f"diag-page-meta-{display_suffix}-{int(time.time())}",
        backend_config=_vector_db_config(),
    )
    print(f"  created corpus: {corpus.name}")
    return corpus.name


def _wait_for_files(corpus_name: str, expected: int = 1, timeout_s: int = 600) -> int:
    """Poll list_rag_files until at least `expected` files are ACTIVE."""
    deadline = time.time() + timeout_s
    last_count = -1
    while time.time() < deadline:
        files = list(rag.list_files(corpus_name=corpus_name))
        active = [f for f in files if str(getattr(f, "file_status", "")).endswith("ACTIVE")
                  or str(getattr(getattr(f, "file_status", None), "state", "")).endswith("ACTIVE")]
        # SDK shape varies — fall back to truthy check on file_status
        if not active:
            active = [
                f for f in files
                if "ACTIVE" in repr(getattr(f, "file_status", ""))
            ]
        if len(active) != last_count:
            print(f"  files seen={len(files)} active={len(active)}")
            last_count = len(active)
        if len(active) >= expected:
            return len(active)
        time.sleep(15)
    raise TimeoutError(f"timed out waiting for {expected} ACTIVE files in {corpus_name}")


def _retrieve_summary(corpus_name: str, query: str) -> dict:
    """Single retrieve_contexts call → summary dict for the variant table."""
    _init_vertex_for(corpus_name)
    cfg = rag.RagRetrievalConfig(top_k=5)
    resp = rag.retrieval_query(
        text=query,
        rag_resources=[rag.RagResource(rag_corpus=corpus_name)],
        rag_retrieval_config=cfg,
    )
    contexts = getattr(getattr(resp, "contexts", None), "contexts", None) or []
    chunks_with_pagespan = 0
    chunks_with_pagenum = 0
    pages_seen = []
    for c in contexts:
        rc = getattr(c, "chunk", None) or getattr(c, "rag_chunk", None)
        if rc is None:
            continue
        ps = getattr(rc, "page_span", None)
        if ps is not None:
            chunks_with_pagespan += 1
            fp = getattr(ps, "first_page", None)
            lp = getattr(ps, "last_page", None)
            if fp or lp:
                chunks_with_pagenum += 1
                pages_seen.append((fp, lp))
    return {
        "n_contexts": len(contexts),
        "n_with_page_span": chunks_with_pagespan,
        "n_with_page_numbers": chunks_with_pagenum,
        "pages": pages_seen[:5],
    }


async def _run_variant(label: str, gcs_pdf: str, **import_kwargs) -> dict:
    """Create a temp corpus, import the PDF with `import_kwargs`, retrieve, delete."""
    print(f"\n--- variant {label} ---  import_kwargs={list(import_kwargs)}")
    corpus_name = _make_corpus(label.lower().replace(" ", "-"))
    try:
        op = await rag.import_files_async(
            corpus_name,
            paths=[gcs_pdf],
            **import_kwargs,
        )
        print(f"  LRO: {op.operation.name}")
        # Wait for the LRO to finish, then for at least one ACTIVE file.
        try:
            await op.result()  # type: ignore[func-returns-value]
        except Exception as e:
            print(f"  op.result() raised (may be benign): {e!r}")
        n = _wait_for_files(corpus_name, expected=1, timeout_s=600)
        print(f"  active files: {n}")
        summary = _retrieve_summary(corpus_name, PROBE_QUERY)
        summary["variant"] = label
        return summary
    finally:
        try:
            from app.rag_corpus import delete_corpus
            delete_corpus(corpus_name)
            print(f"  deleted corpus: {corpus_name}")
        except Exception as e:
            print(f"  WARN delete failed: {e!r}")


async def reingest_experiment(gcs_pdf: str) -> None:
    _section(f"REINGEST EXPERIMENT — pdf={gcs_pdf}")
    layout_parser = _layout_parser_config()
    llm_parser = _llm_parser_cfg()
    chunking_cfg = rag.TransformationConfig(
        chunking_config=rag.ChunkingConfig(chunk_size=1024, chunk_overlap=200)
    )

    summaries: list[dict] = []

    summaries.append(await _run_variant(
        "A_layout_no_chunking",
        gcs_pdf,
        layout_parser=layout_parser,
    ))

    summaries.append(await _run_variant(
        "B_layout_with_chunking",
        gcs_pdf,
        layout_parser=layout_parser,
        transformation_config=chunking_cfg,
    ))

    try:
        summaries.append(await _run_variant(
            "C_llm_parser",
            gcs_pdf,
            llm_parser=llm_parser,
        ))
    except Exception as e:
        print(f"  variant C failed: {e!r}")

    summaries.append(await _run_variant(
        "D_no_parser_default",
        gcs_pdf,
        transformation_config=chunking_cfg,
    ))

    print()
    print("=" * 78)
    print("SUMMARY  (n_with_page_numbers > 0  ⇒  serverless populates page_span)")
    print("=" * 78)
    for s in summaries:
        print(
            f"  {s.get('variant'):<28}  contexts={s['n_contexts']:>2}  "
            f"page_span={s['n_with_page_span']:>2}  "
            f"page_nums={s['n_with_page_numbers']:>2}  "
            f"sample={s['pages']}"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = sys.argv[1:]
    mode = args[0] if args else "inspect"

    print(f"project: {settings.gcp_project_id}   default_location: {settings.gcp_location}")
    print(f"embedding_model: {settings.vertex_rag_embedding_model}")
    print(f"docai processor: {settings.documentai_us_location}/{settings.documentai_us_processor_id}")

    if mode == "inspect":
        probe_retrieve_contexts(CORPUS)
        probe_grounded_generate(CORPUS)
        return

    if mode == "reingest":
        if len(args) < 2:
            print("usage: diag_rag_chunk_metadata.py reingest gs://bucket/file.pdf")
            sys.exit(2)
        asyncio.run(reingest_experiment(args[1]))
        return

    print(f"unknown mode: {mode}")
    sys.exit(2)


if __name__ == "__main__":
    main()
