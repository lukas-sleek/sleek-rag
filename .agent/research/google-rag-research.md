# Google Cloud Managed RAG Research (April 2026)

> Scope: managed RAG offerings that support (1) **citations with page numbers** and (2) **image retrieval from technical documents** (drawings, diagrams, figures from PDFs).
>
> All sources below are dated 2026 unless explicitly flagged otherwise. As of **Google Cloud Next '26 (Apr 2026)**, "Vertex AI" was rebranded to **Gemini Enterprise Agent Platform**. The product surface, APIs, and SDK names still appear under both names in the docs during the transition.

---

## 1. Offerings Overview

There are three relevant managed RAG products, plus the parser that powers all of them:

### 1a. Document AI Layout Parser (the parser)
- "Converts unstructured content into structured, machine-readable information." Combines OCR with Gemini for understanding tables, figures, lists, headers.
- Output is a `Document.chunked_document.chunks` list. Each chunk has a **`pageSpan` field with `pageStart` / `pageEnd`** — this is the load-bearing field for page-number citations.
- Supports a **`returnImages` toggle**: when enabled, figures/images are extracted as descriptive annotation blocks AND the actual image bytes (base64 in JSON) are returned and assigned to the chunk.
- v1.6 (Jan 2026, preview) is powered by Gemini 3.0; v1.0 (GA, Jun 2024) is the stable production version.
- File support: PDF, HTML, DOCX, PPTX, XLSX, XLSM. PDFs up to 500 pages (batch) / 15 pages (online); 20 MB online, 1 GB batch. ([source — last updated 2026-04-24](https://docs.cloud.google.com/document-ai/docs/layout-parse-chunk))

### 1b. Vertex AI / Agent Platform RAG Engine (the developer-friendly RAG)
- "Data framework for context-augmented LLM applications." End-to-end pipeline: ingest -> transform -> embed -> index -> retrieve -> generate.
- A "corpus" is the indexed knowledge base. Native integration with Document AI Layout Parser as a transform option.
- Vector DB choices: **RagManagedDb** (Spanner-backed, default), Vertex AI Vector Search, Feature Store, Weaviate, Pinecone, Vertex AI Search. Serverless mode in public preview (Apr 2026).
- Python SDK: `from vertexai import rag` (`rag.create_corpus`, `rag.import_files`, `rag.retrieval_query`, `Tool.from_retrieval`).
- ([rag-overview, last updated 2026-04-23](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/rag-engine/rag-overview)) ([rag-quickstart, last updated 2026-04-24](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/rag-engine/rag-quickstart))

### 1c. Vertex AI Search (renamed "Agent Search" on parts of the new platform)
- A higher-level managed search/answer product. You upload PDFs to a data store; it parses (digital, OCR, or Layout parser), chunks, indexes, and provides a generative "answer" endpoint with **citations** and **streaming**.
- Layout Parser is GA inside Vertex AI Search and can be enabled with **image annotation** and **table annotation** add-ons; chunks returned by the search API include `pageSpan.pageStart` / `pageSpan.pageEnd` AND, with image annotation enabled, the chunk carries the image bytes plus its description.
- ([parse-chunk-documents, last updated 2026-04-24](https://docs.cloud.google.com/generative-ai-app-builder/docs/parse-chunk-documents)) ([answer endpoint](https://docs.cloud.google.com/generative-ai-app-builder/docs/answer))

### 1d. Roll-your-own multimodal (Vector Search + multimodal embeddings)
- For full control over image retrieval (e.g. CLIP-style embeddings of technical drawings), you bypass the managed RAG and use Vertex AI Vector Search + Multimodal Embeddings + Cloud Storage. Pattern documented in [Towards Data Science, "Multimodal citations with Vertex AI" (Apr 2024 — pre-2026, included only as architectural reference)](https://towardsdatascience.com/multimodal-citations-with-googles-vertex-ai-ebeea75f0d1d/).

---

## 2. Page Citations Capability

| Product | Page numbers in citations? | How |
|---|---|---|
| **Document AI Layout Parser** | Yes | Each chunk has `pageSpan { pageStart, pageEnd }`. ([source 2026-04-24](https://docs.cloud.google.com/document-ai/docs/layout-parse-chunk)) |
| **Vertex AI Search "answer"** | Yes (chunks carry pageSpan; answer can `include_citations=true`) | Citations include doc URI, title, snippet. Page-number metadata is on the underlying chunk and accessible via search results. ([answer docs](https://docs.cloud.google.com/generative-ai-app-builder/docs/answer)) ([oneuptime, 2026-02-17](https://oneuptime.com/blog/post/2026-02-17-how-to-implement-answer-generation-with-citations-in-vertex-ai-search/view)) |
| **Vertex AI RAG Engine** | Indirect | The grounding output fields are `source_uri`, `source_display_name`, `text`, `score`, plus `groundingChunks` and `groundingSupports` (with `start_index`/`end_index` into the answer text, NOT page numbers). The official `rag-output-explained` doc does **not** mention `pageSpan` in the grounding metadata. ([rag-output-explained, last updated 2026-04-23](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/model-reference/rag-output-explained)) → To get page numbers, you must use Layout Parser as the transform AND read pageSpan from the chunk metadata yourself. |

**Bottom line:** Layout Parser is the only Google component that natively emits `pageStart/pageEnd`. Vertex AI Search surfaces it cleanly. RAG Engine requires you to either query the raw chunks or stuff page numbers into chunk text/metadata.

---

## 3. Image Retrieval Capability (technical drawings, diagrams)

| Product | Image retrieval? | How |
|---|---|---|
| **Document AI Layout Parser** | Yes (images extracted) | `returnImages=true` returns figure bytes (base64 in JSON) and a Gemini-generated description of each figure assigned to its chunk. ([layout-parse-chunk, 2026-04-24](https://docs.cloud.google.com/document-ai/docs/layout-parse-chunk)) — Image/table annotation is in **Preview** in some configs. |
| **Vertex AI Search** | Yes (image annotation add-on) | "When image annotation is enabled, a description (annotation) of the image and the image itself are assigned to a chunk." Search results return both. ([parse-chunk-documents, 2026-04-24](https://docs.cloud.google.com/generative-ai-app-builder/docs/parse-chunk-documents)) |
| **Vertex AI RAG Engine** | Partial | Default text-only. With Layout Parser transform you get image *descriptions* into the text index — semantic search hits the description, but image bytes are not surfaced through `retrieveContexts`. For true image-out behavior you'd need a parallel multimodal embeddings index in Vector Search. The use-rag-in-multimodal-live page only mentions Text + Audio for the MemoryCorpus type. ([2026-04-23](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/rag-engine/use-rag-in-multimodal-live)) |

**For technical drawings specifically:** Layout Parser detects figures, generates a Gemini caption of each figure, and emits the bitmap. This works well for diagrams that contain readable text, but for purely visual queries ("find me drawings that look like X") you need a multimodal embeddings index alongside.

---

## 4. The Combination That Hits Both Requirements

Two practical recipes:

**Recipe A — Vertex AI Search (lowest effort):**
- Create a data store; enable Layout Parser with `image annotation = true` and `table annotation = true`.
- Use the `answer` endpoint with `include_citations: true` and streaming on. Iterate `references` to render citations (URI + pageSpan) and inline images per chunk.
- Streaming is supported via a dedicated "Stream answers" feature.

**Recipe B — RAG Engine + Layout Parser (more flexible):**
- `rag.create_corpus(...)` with Layout Parser as the transform and `text-embedding-005` as embeddings.
- Store the chunk's pageSpan in chunk metadata (`rag_file.metadata`) so retrievals carry it.
- For image retrieval, run a parallel pipeline: extract figures from Layout Parser response, embed with Vertex Multimodal Embeddings, store in Vertex AI Vector Search, run retrieval in parallel and merge results before passing to Gemini.

---

## 5. Pricing Breakdown (April 2026)

All amounts USD. Single-source where possible — fall back to Google's published rates only.

### 5a. Parsing (per page)
- **Document AI Layout Parser:** $10 per 1,000 pages (≈ $0.01/page). No volume discount. Includes initial chunking. ([cloud.google.com/document-ai/pricing](https://cloud.google.com/document-ai/pricing); [aiproductivity, 2026](https://aiproductivity.ai/blog/document-ai-cost-comparison/))
- Enterprise OCR (cheaper, no layout): $1.50 / 1k pages, drops to $0.60 / 1k at 5M+ pages/mo.
- Failed requests (4xx/5xx) are not billed.
- A "page" of a DOCX = up to 3,000 chars; an image = 1 page. PDF = literal page.

### 5b. Embeddings
- `text-embedding-005`: ~$0.025 per 1M tokens (effectively free at low volumes within rate limits). ([tokenmix 2026](https://tokenmix.ai/blog/gemini-embedding-001-dimensions-pricing-guide-2026))
- `gemini-embedding-001`: $0.15 / 1M tokens (online), $0.075 / 1M tokens (batch).
- `gemini-embedding-2-preview` (multimodal): $0.20 / 1M text input tokens; image and audio input tiers higher.

### 5c. Vector storage / index
- **RagManagedDb (Spanner backend, RAG Engine default):** Basic = 100 PUs + backup; Scaled = 1,000 PUs autoscaling to 10,000. Spanner pricing flows through to your bill (~$0.90/PU-hour US; thus Basic ≈ $65/mo, Scaled starts ≈ $650/mo before storage). ([rag-engine-billing, 2026-04-23](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/rag-engine/rag-engine-billing))
- **Vertex AI Vector Search:**
  - Index build (batch): $3.00 per GiB processed.
  - Streaming updates: $0.45 per GiB inserted.
  - Index serving: $0.094 / node-hour (e2-standard-2) and up; optimized serving $0.38/node-hour; Bigtable backend $1.20/node-hour. ([nops 2026-03-19](https://www.nops.io/blog/vertex-ai-pricing/))
- A small startup with 1 always-on serving node burns ~$70/month minimum on Vector Search.

### 5d. Retrieval / query
- **`retrieveContexts` (RAG Engine):** No additional LLM charge — you pay only embedding + vector DB costs. ([Google Developer forum, 2026](https://discuss.google.dev/t/vertex-ai-rag-engine-price-retrievecontexts/190665))
- **Vertex AI Search queries:** $1.50–$4.00 per 1,000 queries depending on tier; **+$4.00 / 1,000** for generative answers. Free tier: 10,000 queries/month. Advanced indexing storage: ~$5/GB/month. ([nops 2026; finout 2026](https://www.finout.io/blog/top-16-vertex-services-in-2026))

### 5e. Generation (Gemini)
- Gemini 2.5 Flash: input $0.30 / 1M tokens; output $2.50 / 1M tokens.
- Gemini 2.5 Pro: input $1.25/$2.50 (≤200K / >200K context); output $10/$15 per 1M tokens. ([cloud.google.com/vertex-ai/generative-ai/pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing))

### 5f. Grounding
- "Grounding with your data" (RAG Engine via Tool.from_retrieval): $2.50 per 1,000 grounded requests.
- Grounding with Google Search: $35 per 1,000 grounded prompts.

### Worked example — small startup, 10k pages, 100k queries/month
| Item | Calculation | Cost |
|---|---|---|
| Parse 10k pages with Layout Parser (one-time) | 10 × $10 | $100 |
| Embed with text-embedding-005 (~10M tokens) | 10 × $0.025 | $0.25 |
| RagManagedDb (Basic, ~100 PUs) | Spanner | ~$65/mo |
| 100k queries via RAG Engine + Gemini 2.5 Flash (~2K in / 500 out per query) | 0.2B in × $0.30/M + 0.05B out × $2.50/M | $60 + $125 = $185/mo |
| Grounding charge | 100k / 1k × $2.50 | $250/mo |
| **Total** | | **~$500/mo + $100 one-time parse** |

Switch to Vertex AI Search instead: parse $100 once + 100k × ($4 search + $4 answer) / 1k = $800/mo. RAG Engine is cheaper at this scale; Vertex AI Search wins on time-to-ship.

---

## 6. SDK & Integration

- **Python SDK:** `google-cloud-aiplatform[rag]` plus `from vertexai import rag`. Authentication via `gcloud auth application-default login` for local dev or a service-account JSON. IAM role: `roles/aiplatform.user`. ([rag-quickstart 2026-04-24](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/rag-engine/rag-quickstart))
- **REST:** Full REST API exists (`projects/.../ragCorpora`, `:retrieveContexts`, `:generateContent`).
- **Streaming:** Gemini `generateContent` supports streaming chunks; the multimodal-live endpoint streams server-sent chunks (`async for raw_response in ws`). Vertex AI Search has a dedicated "Stream answers" endpoint.
- Google's ADK (Agent Development Kit) ships a first-party Vertex AI RAG Engine tool integration.

---

## 7. Quotas, Limits, Regional Availability

(Source: [generative AI quotas, 2026-04-24](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/quotas))

- RAG Engine data-management APIs: **60 RPM** per region.
- `RetrieveContexts` API: **600 RPM** per region.
- Concurrent `ImportRagFiles`: **3 RPM** per region; max **10,000 files per import call**.
- Layout Parser: **120 parsing requests / minute** (configurable).
- Embeddings (gecko): 1,500 RPM per region.

**Regional availability (RAG Engine):**
- GA: us-central1, us-east4, europe-west3, europe-west4.
- Preview: ~20 regions including asia-east1, asia-northeast1, additional EU/US.
- **Note:** us-central1, us-east1, us-east4 became allowlist-only for production traffic — contact `vertex-ai-rag-engine-support@google.com`.
- Reports of "RAG Engine does not work in Europe" still appear on Google Developer Forums in 2026 — verify in your target region before committing.

**Document AI Layout Parser limits:**
- Online: 20 MB max file, 15 pages per PDF.
- Batch: 1 GB max file, 500 pages per PDF.

**Security:** VPC-SC and CMEK supported by RAG Engine. Data residency and AXT controls **not** supported.

---

## 8. Limitations & Gotchas

1. **RAG Engine grounding metadata doesn't natively expose page numbers.** Official `rag-output-explained` does not list pageSpan; you must surface it yourself by using Layout Parser as the parser AND reading pageSpan from the parsed chunk metadata you stash in the corpus. Vertex AI Search exposes it cleanly.
2. **Image retrieval in RAG Engine is "image as caption."** Default behavior gives you a Gemini-generated description of each figure but not the image bitmap in the retrieval response. For true visual retrieval of technical drawings you need a parallel Multimodal Embeddings + Vector Search pipeline.
3. **Image annotation in Layout Parser is in Preview** in some SKUs (table+image annotation listed as Preview in the Apr 2026 doc). Contractual GA promises don't apply.
4. **Layout Parser online cap is 15 pages per PDF** — anything larger requires the batch endpoint and async polling.
5. **No volume discount on Layout Parser.** Big PDF backfills get expensive linearly: 1M pages = $10,000 flat.
6. **RAG Engine regional gotcha.** EU support has rough edges; the Vertex AI rebrand to Gemini Enterprise Agent Platform (Apr 2026) is still mid-transition — some console paths and SDK names are out-of-sync.
7. **RagManagedDb is Spanner.** Cheapest tier still costs ~$65/mo idle. For a tiny startup, this is a real floor — Pinecone/Weaviate options can be cheaper at low scale.
8. **Vertex AI Search bills both for the search and the generative answer** ($4 + $4 / 1k qps). Costs explode faster than RAG Engine at high QPS.
9. **Failed Document AI requests are not billed**, but partially-successful ingests (e.g., page 480 of 500 fails) bill for what succeeded.
10. **Streaming + grounding metadata interplay:** when streaming, grounding metadata typically arrives in the final chunk. UI design needs to handle citation rendering after the answer is fully streamed.

---

## 9. Recommendation for sleek-rag

Given the constraint set (Python/FastAPI backend, Supabase storage, requirement for page citations + image retrieval of technical drawings, small-team startup):

**Use Document AI Layout Parser directly + roll your own retrieval in Supabase pgvector.** This keeps the existing Supabase architecture, gives you `pageStart`/`pageEnd` natively, and gives you image bytes to store in Supabase Storage. You pay only $10 / 1k pages for parsing — no Spanner floor, no RAG Engine vendor lock-in, no Vertex AI Search query fees.

If the pure managed path is preferred:
- **Vertex AI Search with Layout Parser + image annotation + streaming answer endpoint** is the single-API option that ships fastest. Plan ~$800/mo at 100k queries.
- **Vertex AI / Agent Platform RAG Engine + Layout Parser** is the middle ground but you lose the project's "no vendor opinionation" stance and the page-number plumbing isn't free.

For technical drawings specifically: the Gemini caption-of-figure approach (Layout Parser default) is sufficient for searchable engineering diagrams **with** legible labels. For photos/blueprints with little text, add a Multimodal Embeddings + Vector Search side-index — that is unambiguously a custom build, not a managed offering.

---

## Sources (all verified 2026 unless flagged)

- [Document AI Layout Parser docs (2026-04-24)](https://docs.cloud.google.com/document-ai/docs/layout-parse-chunk)
- [Layout Parser + Vertex AI RAG Engine integration (2026-04-23)](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/rag-engine/layout-parser-integration)
- [Vertex AI RAG Engine overview (2026-04-23)](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/rag-engine/rag-overview)
- [Gemini Enterprise Agent Platform RAG Engine overview (2026-04-27)](https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/rag-engine/rag-overview)
- [RAG Engine billing (2026-04-23)](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/rag-engine/rag-engine-billing)
- [RAG Engine quickstart (2026-04-24)](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/rag-engine/rag-quickstart)
- [RAG output schema (2026-04-23)](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/model-reference/rag-output-explained)
- [Generative AI quotas (2026-04-24)](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/quotas)
- [Vertex AI Search parse and chunk docs (2026-04-24)](https://docs.cloud.google.com/generative-ai-app-builder/docs/parse-chunk-documents)
- [Vertex AI Search "Get answers and follow-ups"](https://docs.cloud.google.com/generative-ai-app-builder/docs/answer)
- [Document AI pricing](https://cloud.google.com/document-ai/pricing)
- [Vertex AI generative AI pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing)
- [Vertex AI pricing (2026 guide, nops, 2026-03-19)](https://www.nops.io/blog/vertex-ai-pricing/)
- [Document AI cost comparison 2026 (aiproductivity)](https://aiproductivity.ai/blog/document-ai-cost-comparison/)
- [Vertex AI Search pricing summary 2026 (finout)](https://www.finout.io/blog/top-16-vertex-services-in-2026)
- [gemini-embedding-001 pricing 2026 (tokenmix)](https://tokenmix.ai/blog/gemini-embedding-001-dimensions-pricing-guide-2026)
- [Vertex AI Search citations (oneuptime, 2026-02-17)](https://oneuptime.com/blog/post/2026-02-17-how-to-implement-answer-generation-with-citations-in-vertex-ai-search/view)
- [Google Cloud Next '26 — Gemini Enterprise Agent Platform launch (HPCwire, 2026-04-23)](https://www.hpcwire.com/aiwire/2026/04/23/google-unveils-gemini-enterprise-agent-platform/)
- [Use RAG in Gemini Live API (2026-04-23)](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/rag-engine/use-rag-in-multimodal-live)
- (Architectural reference, pre-2026) [Multimodal Citations with Vertex AI, Towards Data Science (2024-04-29)](https://towardsdatascience.com/multimodal-citations-with-googles-vertex-ai-ebeea75f0d1d/)
