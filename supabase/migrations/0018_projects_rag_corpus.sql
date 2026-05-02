-- Migration 0018: track Vertex AI RAG Engine corpus per project.
--
-- Plan 18.1 T4: prepare schema for the Vertex RAG migration. Every project
-- gets exactly one corpus (Q4 in 18.0 master spec), lazy-created on first
-- file upload by 18.2. NULL until then; existing projects with no files
-- stay NULL and lazy-create on next upload. Project deletion in 18.x
-- cascades into rag.delete_corpus() + GCS prefix delete.

alter table public.projects
  add column rag_corpus_name text default null;

comment on column public.projects.rag_corpus_name is
  'Vertex AI RAG Engine corpus resource name (projects/.../ragCorpora/...). '
  'Lazy-created on first file upload (plan 18.2). NULL for projects with no '
  'files yet.';
