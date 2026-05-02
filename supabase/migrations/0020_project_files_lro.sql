-- Migration 0020: track in-flight rag.import_files LRO + repurpose gcs_blob_path.
--
-- Plan 18.2 T4. The new ingest flow at upload time:
--   1. Uploads the canonical PDF to gs://sleek-rag-files-{env}/{user}/{project}/{file}/original.pdf
--   2. Triggers rag.import_files_async() and stores the LRO operation name here
--   3. The LRO poller (T5) flips status parsing -> ready/failed and clears
--      ingest_lro_name once the operation resolves.
--
-- gcs_blob_path semantics shift from "Supabase Storage object key" (transient,
-- copied into a staging bucket by the old worker) to "permanent GCS URI of the
-- canonical original file". Pre-18.x rows still hold Supabase Storage keys;
-- they get cleaned up in 18.7 alongside the document_chunks tear-down.

alter table public.project_files
  add column ingest_lro_name text default null;

comment on column public.project_files.ingest_lro_name is
  'Vertex AI long-running operation name (projects/.../operations/...) for an '
  'in-flight rag.import_files import. Set at upload time, cleared by the LRO '
  'poller once status moves to ready or failed.';

comment on column public.project_files.gcs_blob_path is
  'GCS URI of the canonical original file '
  '(gs://{bucket}/{user_id}/{project_id}/{file_id}/original.pdf) from plan '
  '18.2 onwards. Pre-18.x rows hold the legacy Supabase Storage object key '
  'and are cleaned up in 18.7.';
