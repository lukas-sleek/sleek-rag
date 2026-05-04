# Vertex AI Managed Vector Search — `URL_REJECTED Reason 54` on retrieval

**Date:** 2026-05-04
**Status:** open / blocking chat
**Region:** `us-central1` (only region the RAG Engine **serverless** mode is available in — locked by `.env` `GCP_LOCATION`, see `backend/app/config.py:35` and the comment "us-central1 only — serverless preview is restricted to this region")
**Project:** `sleek-rag` (`1007445049099`)
**Corpus reproducing now:** `projects/1007445049099/locations/us-central1/ragCorpora/7928763065846202368`
  display_name: `sleek-rag-37d4d87d-8d61-48db-ba04-67cf15a667c1`

---

## User-visible symptom

Chat returns `_⚠️ Die Antwort konnte gerade nicht erzeugt werden. Bitte in ein paar Sekunden erneut versuchen._` (the `_friendly_gemini_error` toast in `backend/app/routers/chats.py:54-61`). User reports the failure on a single question (`"In welcher Phase werden Ingenieurdienstleitungen angefragt?"`) — i.e. not a burst-traffic / multi-question scenario.

## Backend exception (verbatim, last 5 frames + cause)

```
google.adk.flows.llm_flows.base_llm_flow._call_llm_async
google.adk.flows.llm_flows.base_llm_flow._call_llm_with_tracing
google.adk.flows.llm_flows.base_llm_flow._run_and_handle_error
google.adk.models.google_llm.generate_content_async (line 245)
google.genai.models.generate_content                (line 8349)
google.genai.errors.ClientError: 400 FAILED_PRECONDITION
{
  'error': {
    'code': 400,
    'message': 'Failed to process Rag Managed Vertex Vector Search response.; '
               'Failed to parse the Harpoon FetchReply from Vertex Vector Search: go/debugproto\n'
               'Data {\n'
               '  ID: 9641528383096322651\n'
               '  Url: "https://vectorsearch.googleapis.com/v1beta/projects/sleek-rag/'
               'locations/us-central1/collections/vertex-rag-7928763065846202368/dataObjects:search"\n'
               '  Status { State: URL_REJECTED Reason: 54 }\n'
               '  Events { Msg: "QPS or BW/in or BW/out quota exceeded" }\n'
               '  RequestorID: "harpoon-vertex-rag-managed-vertex-vector-search"\n'
               '  HttpProtocol: PROTO_POST\n'
               '}'
  },
  'status': 'FAILED_PRECONDITION'
}
```

The error message text is misleading: it refers to "QPS or BW … quota exceeded" but *deterministic per-query* failure (below) rules out a quota cause. `URL_REJECTED Reason 54` is Google's internal `harpoon-vertex-rag-managed-vertex-vector-search` rejection code — surfaced as a generic 400 to the SDK.

---

## Update 2026-05-04 11:40 — confirmed Google-side

After this report was first written, the user reproduced the same
`URL_REJECTED Reason 54` from **Agent Builder UI** itself (Google's own
console) for query `"Welche Bauherren sind beteiligt?"`:

```
Anfrage-ID: 17663345837026289756
Status: 400, Fehlercode: 9
Url: https://vectorsearch.googleapis.com/v1beta/projects/sleek-rag/locations/us-central1/collections/vertex-rag-7928763065846202368/dataObjects:search
Status { State: URL_REJECTED Reason: 54 }
Events { Msg: "QPS or BW/in or BW/out quota exceeded" }
RequestorID: "harpoon-vertex-rag-managed-vertex-vector-search"
```

Independent verifications from this box:
- Identical error when calling the public `aiplatform.googleapis.com:retrieveContexts`
  endpoint authenticated as `admin@sleek.de` (the same identity Agent Builder UI uses).
- Identical error running the user's verbatim `genai.Client(vertexai=True, ...)`
  +  `Tool(retrieval=Retrieval(vertex_rag_store=...))` snippet.

So it's not our service account, not our SDK wrapper, not our safety/temperature
config, and not our network egress. It also is **not** strictly deterministic per
query — the earlier 25–35 % success rate was a snapshot of partial degradation
that has since drifted closer to total. **The Managed Vector Search collection
backing this corpus is degraded on Google's side.**

## What we ruled out

### 1. Cold-corpus / "still indexing"
- The corpus was created at `2026-05-04 09:14:03 UTC`.
- Failure reproduced 7 min, 15 min, and 25 min after creation — same behaviour.
- All 4 ragFiles confirmed present in `rag.list_files(corpus)`. ragFile IDs match what we stored in `project_files.rag_file_name`.

### 2. State drift between our DB and Vertex
Diagnostic dump landed in journal via `_dump_rag_state` (`backend/app/routers/chats.py:64-110`, added in commit `7512385`):

```json
{
  "corpus_name": "projects/1007445049099/locations/us-central1/ragCorpora/7928763065846202368",
  "project_id": "37d4d87d-8d61-48db-ba04-67cf15a667c1",
  "db_files": [
    { "filename": "HO_Teil_A_…def.pdf",          "status": "ready", "page_count": 7,  "rag_file_name": "…/ragFiles/5690617325643346933" },
    { "filename": "HO_Teil_C1_…def_Word.docx",   "status": "ready", "page_count": 11, "rag_file_name": "…/ragFiles/5690617335396136369" },
    { "filename": "HO_Teil_B_…def.pdf",          "status": "ready", "page_count": 27, "rag_file_name": "…/ragFiles/5690617469962716422" },
    { "filename": "HO_Teil_C2_…def_Excel.xlsx",  "status": "ready", "page_count": 5,  "rag_file_name": "…/ragFiles/5690619162284710229" }
  ],
  "vertex_files": [ … 4 files, IDs match db_files exactly … ]
}
```

Our DB and Vertex agree on file identity and "ready"-ness. Wiring is correct.

### 3. Per-project quota
Cloud Console quota dashboard for `aiplatform.googleapis.com` and `vectorsearch.googleapis.com` shows usage well under any documented limit. More importantly, the failure pattern (below) is **per-query deterministic**, not load-driven.

### 4. Embedding API health
Direct `TextEmbeddingModel.from_pretrained("text-multilingual-embedding-002").get_embeddings([...])` against the same project/region returns 768-dim vectors for every test query (10/10 OK in <1 s each). The embedding stage is fine.

### 5. Code path / ADK / generate_content
Bypassing the entire Gemini grounding wrapper and calling `vertexai.rag.retrieval_query(...)` directly produces the **same** error. Reproducer below.

---

## The actual pattern: deterministic per-query failure

Same Vertex serverless RAG project, same corpus, sequential calls (~1 s apart). Not just transient noise — `"Phase"` fails 6/6 in a row, `"Bauherr"` succeeds 6/6 in a row.

| Query                          | Result                                    |
|---|---|
| `"Phase"`                      | FAIL × 6 (URL_REJECTED Reason 54)        |
| `"Ingenieurdienstleitungen"`   | FAIL                                      |
| `"Ingenieurleistungen"`        | FAIL                                      |
| `"Ingenieurdienstleistungen"`  | FAIL                                      |
| `"Ingenieur"`                  | FAIL                                      |
| `"Tunnel"`                     | FAIL                                      |
| `"Honorar"`                    | FAIL (on `7928763065846202368`); OK on `4921062202204487680/diag-page-meta-b…` |
| `"Termine"`                    | FAIL                                      |
| `"der"`, `"x"`                 | FAIL                                      |
| `"Bauherr"`                    | OK — 10 contexts                         |
| `"Architekt"`                  | OK — 10 contexts                         |

Pattern is stable across **multiple corpora in the same project** (the four `diag-page-meta-*` corpora from 2026-05-02 + the new `sleek-rag-37d4d87d-…` from 2026-05-04 — different `vector-rag-{numeric}/dataObjects:search` URLs, all same behaviour).

Working hypothesis: a subset of shards in this project's Managed Vector Search backing is unhealthy. Vertex RAG Engine's serverless mode (`RagManagedDb` — no `vector_db` field on the corpus, see `corpus.backend_config.vector_db == None` in our get_corpus probe) shares serving infrastructure across corpora *within a project*. Embeddings whose ANN routing lands on a broken shard are rejected with `Reason 54`; embeddings hitting a healthy shard return.

---

## Repro script (run on the box)

```bash
cd /home/lukasthomas/sleek-rag/backend && \
  GOOGLE_APPLICATION_CREDENTIALS=$(./venv/bin/python3 -c \
    "from app.config import settings; print(settings.gcp_service_account_json_path)") \
  ./venv/bin/python3 <<'PY' 2>&1 | grep -vE 'FutureWarning|warnings.warn'
from app.config import settings
import vertexai
from vertexai import rag
import time
vertexai.init(project=settings.gcp_project_id, location=settings.gcp_location)
corpus = "projects/1007445049099/locations/us-central1/ragCorpora/7928763065846202368"
for q in ["Phase","Phase","Phase","Bauherr","Honorar","Tunnel","Architekt","Termine","der","x","Ingenieur"]:
    t = time.time()
    try:
        r = rag.retrieval_query(rag_resources=[rag.RagResource(rag_corpus=corpus)], text=q)
        n = len(r.contexts.contexts) if hasattr(r, "contexts") else 0
        print(f"OK    {time.time()-t:5.2f}s n={n:2d}  {q!r}")
    except Exception as e:
        msg = str(e).split("Msg:")[-1].split('"')[1] if 'Msg:' in str(e) else type(e).__name__
        print(f"FAIL  {time.time()-t:5.2f}s          {q!r}  -> {msg}")
PY
```

Expected: a stable 25–35 % success rate, deterministic per query.

---

## Constraint that limits remediation

`.env`/`.env.example`:

```
# us-central1 only — serverless preview is restricted to this region.
GCP_LOCATION=us-central1
```

Confirmed by Google's docs (Vertex AI RAG Engine — "Serverless mode availability"): RagManagedDb is **only** in `us-central1`. So "switch region" is not actually a remediation — it would require leaving serverless mode and provisioning explicit Vector Search indexes (full migration off `RagManagedDb`).

---

## What to investigate next (open questions)

1. **Is the rejection deterministic on the embedding vector or on the raw query text?**
   Currently we only know "same query → same outcome". If the embedding is what routes to a broken shard, two semantically-similar queries should fail together. Probe: feed `m.get_embeddings(["Phase", "Phase "])` (with trailing space) and compare cosine similarity vs. their retrieval outcomes; also feed exact translations and minor variants.

2. **Cross-project / cross-tenant scope.**
   Provision a *throwaway* GCP project, enable Vertex AI, create one tiny corpus with one txt file, and run the same probe. If it works there, the broken shard pool is bound to *this* project's allocation in the shared serving fleet — strongest evidence for a Google-side reassignment fix.

3. **Vertex internal status / Issue Tracker.**
   `URL_REJECTED Reason 54` and `harpoon-vertex-rag-managed-vertex-vector-search` are internal identifiers. Search public-issue-tracker.cloud.google.com for "RagManagedDb URL_REJECTED" / "harpoon-vertex-rag" / "Reason: 54". Recently? Open a fresh issue with the corpus IDs above + this report attached.

4. **Stale `diag-page-meta-*` corpora.**
   Four diagnostic corpora from 2026-05-02 still exist (`rag.list_corpora()`). They reproduce the same failure pattern, so they're useful for diagnosis right now. Plan to delete them once a support ticket is filed and acknowledged.

5. **Workaround sanity check.**
   Confirm that direct `rag.retrieval_query` against the corpus, when it succeeds, returns *correct* contexts (we already saw this works for "Bauherr" / "Architekt" — both returned 10 contexts containing actual document text). So the index data itself is intact; only retrieval routing is broken.

6. **Does the `text-embedding-005` or `gemini-embedding-001` corpus path behave the same?**
   Recreate one tiny corpus with `vertex_rag_embedding_model="text-embedding-005"`, import one PDF, retrieve. If the failure pattern follows: definitely shard-pool layer, embedding model irrelevant. If it doesn't: embedding-routing artifact.

---

## Files & commits in this thread

- `backend/app/routers/chats.py` — added `_dump_rag_state(corpus_name, project_id)` + invocation in the stream-fail except block. Commit `7512385`.
- `backend/app/auth.py` — broader auth-error logging (separate prior debug). Commit `442cd8b`.
- This report: `.agent/incidents/2026-05-04_vertex_url_rejected_reason_54.md`.

## Pull current journal slice (single command)

```bash
journalctl --user -u sleek-rag-backend --since "30 minutes ago" --no-pager \
  | grep -A60 'rag state for chat='
```

## Pull the bare exception only

```bash
journalctl --user -u sleek-rag-backend --since "30 minutes ago" --no-pager \
  | grep -E 'ClientError|FAILED_PRECONDITION|URL_REJECTED|adk stream failed'
```
