-- Migration 0019: track Vertex AI RAG Engine file resource per uploaded file.
--
-- Plan 18.1 T5: companion to 0018. Once the rag.import_files LRO completes
-- (18.2), the resulting RagFile resource name is persisted here so future
-- delete / re-import operations can reference it directly. NULL while the
-- import is still in flight or if the file pre-dates the migration.

alter table public.project_files
  add column rag_file_name text default null;

comment on column public.project_files.rag_file_name is
  'Vertex AI RAG Engine file resource name '
  '(projects/.../ragCorpora/.../ragFiles/...) once the import LRO completes. '
  'NULL until ready or if the file was uploaded before plan 18.x.';
