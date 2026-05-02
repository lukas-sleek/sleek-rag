"""Smoke: DocAI Layout walk → in-memory dense retrieval → Gemini answer with cites.

Validates the proposed pipeline end-to-end on the user's 4 SIA PDFs and 11
benchmark questions, without touching any DB or Vertex RAG Engine.

Pipeline per PDF:
  1. Download from GCS (cached on disk).
  2. DocAI Layout Parser, EU region, processor version pinned to v1.0.
  3. Walk `document_layout.blocks` → per-(page, heading_path) chunks.
  4. Embed each chunk with gemini-embedding-001 (768d) via OpenAI-compatible.

For each of 11 questions:
  5. Embed question, cosine-rank chunks, take top-K.
  6. Prompt Gemini 2.5 Flash with the chunks numbered [1]..[K]. Ask it to
     answer in Hochdeutsch and cite chunk indices.
  7. Map cited indices → (filename, page, heading) and print.

Run:
    backend/venv/bin/python scripts/smoke_pdf_qa.py

Cached parses live in scripts/.cache/smoke_pdf_qa/ — delete the dir to redo.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from google.cloud import documentai_v1beta3 as documentai
from google.cloud import storage
from google.oauth2 import service_account
from openai import OpenAI

# Ensure backend is importable for settings
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import settings  # noqa: E402

CACHE_DIR = Path(__file__).resolve().parent / ".cache" / "smoke_pdf_qa"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Pin to v1.0 for EU residency (v1.6 routes via global Gemini endpoint)
PROCESSOR_VERSION = "pretrained-layout-parser-v1.0-2024-06-03"

PDF_URLS = [
    "https://storage.googleapis.com/sleek-rag-files-dev/84747b27-a193-452f-a200-74e5a83feaee/09997640-d19d-4b17-a0fd-9b3190b78fc3/5ce1d487-922b-477c-bf8f-87748ba4acdc/HO_Teil_A_Sudi-Areal-Infrastruktur_def.pdf",
    "https://storage.googleapis.com/sleek-rag-files-dev/84747b27-a193-452f-a200-74e5a83feaee/09997640-d19d-4b17-a0fd-9b3190b78fc3/fd318522-f090-452a-9a1b-4f4b5de318e9/HO_Teil_B_Sudi-Areal-Infrastruktur_def.pdf",
    "https://storage.googleapis.com/sleek-rag-files-dev/84747b27-a193-452f-a200-74e5a83feaee/09997640-d19d-4b17-a0fd-9b3190b78fc3/e90f8cf7-51f6-43dd-b419-1dbfa42d9471/HO_Teil_C1_Sudi-Areal-Infrastruktur_def_Word.pdf",
    "https://storage.googleapis.com/sleek-rag-files-dev/84747b27-a193-452f-a200-74e5a83feaee/09997640-d19d-4b17-a0fd-9b3190b78fc3/22aee86e-cabd-4728-9aa7-84cdb9fa5791/HO_Teil_C2_Sudi-Areal-Infrastruktur_def_Excel.pdf",
]

QUESTIONS = [
    "In welcher Phase werden Ingenieurdienstleitungen angefragt?",
    "Welche Bauherren sind beteiligt?",
    "Wie heisst der Projektleiter?",
    "Welche Termine sind vorgesehen? Gibt es zwingende Meilensteine für z.B. Zwischentermine, Gleisschlagwochenenden oder ähnliche?",
    "Was ist die Bausumme?",
    "Welche Drittprojekte tangieren den Perimeter?",
    "Welche Rahmenbedingungen betreffen das Projekt hinsichtlich Termine, Bauzeit oder ähnlichem?",
    "Welche Elemente sind vom Bauprojekt zu überarbeiten? Wie viel Stunden sind dafür in der Ausschreibung vorgesehen?",
    "Welche Elemente sind im Ausführungsprojekt zu überabreiten oder zu ändern?",
    "Ist die Vermessung Bestandteil unseres Auftrags oder ist diese nur zu koordinieren?",
    "Steht in den Plänen irgendwo der Kommentar \"Ist in einer späteren Phase zu Detaillieren.\" oder etwas ähnliches?",
]

TOP_K = 14

# ---------- chunker ----------
SKIP_TYPES = {"header", "footer"}
H1 = re.compile(r"^\d+\s")
H2 = re.compile(r"^\d+\.\d+\s")
H3 = re.compile(r"^\d+\.\d+\.\d+\s")
NOISE = re.compile(r"^\d+(/\d+)?$")


def heading_level(text: str, default: int) -> int:
    if H3.match(text):
        return 3
    if H2.match(text):
        return 2
    if H1.match(text):
        return 1
    return default


def render_table_md(tb: Any) -> str:
    def cell_text(cell: Any) -> str:
        parts: list[str] = []
        for cb in cell.blocks:
            t = (cb.text_block.text or "").strip()
            if t:
                parts.append(t)
        return " ".join(parts)

    def row_md(r: Any) -> str:
        return "| " + " | ".join(cell_text(c) for c in r.cells) + " |"

    rows: list[str] = []
    if tb.header_rows:
        for r in tb.header_rows:
            rows.append(row_md(r))
        n = len(tb.header_rows[0].cells)
        rows.append("|" + "|".join(["---"] * n) + "|")
    for r in tb.body_rows:
        rows.append(row_md(r))
    return "\n".join(rows)


def collect_blocks(blocks, heading_stack, buf):
    for b in blocks:
        ps = b.page_span
        # text_block?
        tb = b.text_block
        if tb.type_ or tb.text or tb.blocks:
            if tb.type_ in SKIP_TYPES:
                continue
            if tb.type_ and tb.type_.startswith("heading"):
                try:
                    declared = int(tb.type_.split("-")[1])
                except (IndexError, ValueError):
                    declared = 1
                lvl = heading_level(tb.text, declared)
                stack = [(l, t) for l, t in heading_stack if l < lvl]
                stack.append((lvl, tb.text.strip()))
                collect_blocks(tb.blocks, stack, buf)
            else:
                t = (tb.text or "").strip()
                if t and not NOISE.match(t) and len(t) >= 4:
                    buf[(ps.page_start, tuple(heading_stack))].append(t)
                if tb.blocks:
                    collect_blocks(tb.blocks, heading_stack, buf)
            continue
        # table_block?
        table = b.table_block
        if table.header_rows or table.body_rows:
            md = render_table_md(table)
            if ps.page_end == ps.page_start:
                tag = f"[Tabelle Seite {ps.page_start}]\n"
            else:
                tag = f"[Tabelle Seiten {ps.page_start}-{ps.page_end}]\n"
            buf[(ps.page_start, tuple(heading_stack))].append(tag + md)
            continue
        # list_block?
        lst = b.list_block
        if lst.list_entries:
            for e in lst.list_entries:
                collect_blocks(e.blocks, heading_stack, buf)


def build_chunks(doc, *, target_chars=2500, hard_cap=4000) -> list[dict]:
    buf: dict[tuple[int, tuple], list[str]] = defaultdict(list)
    collect_blocks(doc.document_layout.blocks, [], buf)
    chunks: list[dict] = []
    for (page, h_path), texts in sorted(buf.items()):
        head = " > ".join(t for _, t in h_path) or "(kein Abschnitt)"
        prefix = f"[Seite {page}]\n{head}\n\n"
        joined = "\n\n".join(texts)
        if len(prefix + joined) <= hard_cap:
            chunks.append({"text": prefix + joined, "page": page, "heading": head})
            continue
        cur = ""
        for p in texts:
            if cur and len(cur) + len(p) + 2 > target_chars:
                chunks.append({"text": prefix + cur, "page": page, "heading": head})
                cur = p
            else:
                cur = (cur + "\n\n" + p) if cur else p
        if cur:
            chunks.append({"text": prefix + cur, "page": page, "heading": head})
    return chunks


# ---------- GCS download ----------
def download_pdf(url: str, dest: Path, gcs: storage.Client) -> None:
    if dest.exists():
        return
    # gs:// translation: https://storage.googleapis.com/<bucket>/<key>
    prefix = "https://storage.googleapis.com/"
    assert url.startswith(prefix), url
    bucket_name, _, key = url[len(prefix):].partition("/")
    blob = gcs.bucket(bucket_name).blob(key)
    blob.download_to_filename(str(dest))


# ---------- DocAI ----------
def docai_parse(pdf_bytes: bytes) -> Any:
    creds = service_account.Credentials.from_service_account_file(
        settings.gcp_service_account_json_path
    )
    opts = {"api_endpoint": f"{settings.documentai_location}-documentai.googleapis.com"}
    client = documentai.DocumentProcessorServiceClient(
        credentials=creds, client_options=opts
    )
    name = (
        f"projects/{settings.gcp_project_id}"
        f"/locations/{settings.documentai_location}"
        f"/processors/{settings.documentai_processor_id}"
        f"/processorVersions/{PROCESSOR_VERSION}"
    )
    req = documentai.ProcessRequest(
        name=name,
        raw_document=documentai.RawDocument(
            content=pdf_bytes, mime_type="application/pdf"
        ),
        process_options=documentai.ProcessOptions(
            layout_config=documentai.ProcessOptions.LayoutConfig()
        ),
    )
    return client.process_document(request=req).document


# ---------- Embeddings (OpenAI-compatible Gemini endpoint) ----------
def gemini_openai_client() -> OpenAI:
    return OpenAI(
        api_key=settings.gemini_api_key,
        base_url=settings.gemini_base_url,
    )


def embed_batch(client: OpenAI, texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    BATCH = 64
    for i in range(0, len(texts), BATCH):
        sub = texts[i : i + BATCH]
        for attempt in range(4):
            try:
                resp = client.embeddings.create(
                    model=settings.gemini_embedding_model,
                    input=sub,
                    dimensions=settings.gemini_embedding_dim,
                )
                out.extend(d.embedding for d in resp.data)
                break
            except Exception as exc:  # noqa: BLE001
                wait = 2 ** attempt
                print(f"  embed retry {attempt+1}: {exc} (sleep {wait}s)", file=sys.stderr)
                time.sleep(wait)
        else:
            raise RuntimeError("embed batch failed after retries")
    return out


# ---------- per-PDF cache ----------
def parse_pdf(url: str, gcs: storage.Client) -> dict:
    name = url.rsplit("/", 1)[-1]
    pdf_path = CACHE_DIR / name
    chunks_path = CACHE_DIR / f"{name}.chunks.json"
    if chunks_path.exists():
        return {"filename": name, "chunks": json.loads(chunks_path.read_text())}
    download_pdf(url, pdf_path, gcs)
    print(f"Parsing {name} via DocAI ({PROCESSOR_VERSION})...")
    t0 = time.time()
    doc = docai_parse(pdf_path.read_bytes())
    chunks = build_chunks(doc)
    print(f"  {len(chunks)} chunks in {time.time()-t0:.1f}s")
    chunks_path.write_text(json.dumps(chunks, ensure_ascii=False))
    return {"filename": name, "chunks": chunks}


# ---------- retrieval + answer ----------
ANSWER_PROMPT = """\
Du bist ein Fachreferent. Beantworte die Frage präzise auf Hochdeutsch in einem
einzigen Fliesstext ohne Markdown-Bullets oder Tabellen. Stütze dich
ausschliesslich auf die nummerierten Auszüge unten und zitiere die genutzten
Quellen direkt im Text in der Form [N], wobei N der Index des Auszugs ist.
Wenn die Antwort nicht aus den Auszügen ableitbar ist, sage das offen.

Frage: {question}

Auszüge:
{ctx}

Antworte nun:"""


def answer(client: OpenAI, question: str, ctx_chunks: list[dict]) -> str:
    rendered = []
    for i, c in enumerate(ctx_chunks, 1):
        rendered.append(f"[{i}] (Datei: {c['filename']}, Seite {c['page']}, Abschnitt: {c['heading']})\n{c['text']}")
    prompt = ANSWER_PROMPT.format(question=question, ctx="\n\n---\n\n".join(rendered))
    resp = client.chat.completions.create(
        model=settings.gemini_chat_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return resp.choices[0].message.content or ""


def cosine_topk(qv: np.ndarray, M: np.ndarray, k: int) -> list[int]:
    qn = qv / (np.linalg.norm(qv) + 1e-9)
    Mn = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
    scores = Mn @ qn
    return np.argsort(-scores)[:k].tolist()


def main() -> int:
    if not settings.gcp_project_id or not settings.documentai_processor_id:
        print("ERROR: GCP_PROJECT_ID and DOCUMENTAI_PROCESSOR_ID must be set.", file=sys.stderr)
        return 2
    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY must be set.", file=sys.stderr)
        return 2

    gcs = storage.Client(
        project=settings.gcp_project_id,
        credentials=service_account.Credentials.from_service_account_file(
            settings.gcp_service_account_json_path
        ),
    )
    openai_cli = gemini_openai_client()

    # 1. Parse all PDFs
    parsed = [parse_pdf(u, gcs) for u in PDF_URLS]

    # 2. Flatten chunks with provenance
    all_chunks: list[dict] = []
    for p in parsed:
        for c in p["chunks"]:
            all_chunks.append({**c, "filename": p["filename"]})
    print(f"\nTotal chunks across {len(parsed)} PDFs: {len(all_chunks)}")
    by_pdf = defaultdict(int)
    for c in all_chunks:
        by_pdf[c["filename"]] += 1
    for k, v in by_pdf.items():
        print(f"  {k}: {v} chunks")

    # 3. Embed all chunks
    embeds_path = CACHE_DIR / "embeds.npy"
    chunks_meta_path = CACHE_DIR / "embeds_meta.json"
    if embeds_path.exists() and chunks_meta_path.exists():
        M = np.load(embeds_path)
        # Sanity: dims must match current chunk count
        if M.shape[0] == len(all_chunks):
            print(f"Loaded {M.shape} embedding cache")
        else:
            M = None
    else:
        M = None
    if M is None:
        print(f"\nEmbedding {len(all_chunks)} chunks with {settings.gemini_embedding_model}...")
        t0 = time.time()
        vecs = embed_batch(openai_cli, [c["text"] for c in all_chunks])
        M = np.array(vecs, dtype=np.float32)
        np.save(embeds_path, M)
        chunks_meta_path.write_text(json.dumps([{k: v for k, v in c.items() if k != "text"} for c in all_chunks], ensure_ascii=False))
        print(f"  done in {time.time()-t0:.1f}s ({M.shape})")

    # 4. Run questions
    print("\n" + "=" * 80)
    for qi, q in enumerate(QUESTIONS, 1):
        print(f"\n--- Q{qi}: {q}")
        qv = np.array(embed_batch(openai_cli, [q])[0], dtype=np.float32)
        idxs = cosine_topk(qv, M, TOP_K)
        ctx = [all_chunks[i] for i in idxs]
        ans = answer(openai_cli, q, ctx)
        print(ans)
        # Citation footer: parse [N] references in answer
        cited = sorted({int(m) for m in re.findall(r"\[(\d+)\]", ans)})
        if cited:
            print("\nQuellen:")
            for n in cited:
                if 1 <= n <= len(ctx):
                    c = ctx[n - 1]
                    print(f"  [{n}] {c['filename']} · S. {c['page']} · {c['heading']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
