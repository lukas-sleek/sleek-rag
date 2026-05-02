-- Migration 0008: retrieval RPC helpers used by the chat endpoint.
-- match_chunks: cosine-similarity ANN with optional block_type filter.
-- chunks_by_heading_prefix: heading_path[] containment for "Section 3.6" queries.

create or replace function public.match_chunks(
  p_project_id uuid,
  p_embedding vector(768),
  p_top_k int default 8,
  p_block_type text default null
) returns table (
  id uuid,
  file_id uuid,
  project_id uuid,
  content text,
  page_start int,
  page_end int,
  figure_label text,
  block_type text,
  filename text,
  similarity float
) language sql stable as $$
  select c.id,
         c.file_id,
         c.project_id,
         c.content,
         c.page_start,
         c.page_end,
         c.figure_label,
         c.block_type,
         pf.filename,
         1 - (c.embedding <=> p_embedding) as similarity
  from public.document_chunks c
  join public.project_files pf on pf.id = c.file_id
  where c.project_id = p_project_id
    and (p_block_type is null or c.block_type = p_block_type)
  order by c.embedding <=> p_embedding
  limit p_top_k;
$$;

create or replace function public.chunks_by_heading_prefix(
  p_project_id uuid,
  p_prefix text
) returns table (
  id uuid,
  file_id uuid,
  project_id uuid,
  content text,
  page_start int,
  page_end int,
  figure_label text,
  block_type text,
  filename text
) language sql stable as $$
  select c.id,
         c.file_id,
         c.project_id,
         c.content,
         c.page_start,
         c.page_end,
         c.figure_label,
         c.block_type,
         pf.filename
  from public.document_chunks c
  join public.project_files pf on pf.id = c.file_id
  where c.project_id = p_project_id
    and c.heading_path is not null
    and exists (
      select 1 from unnest(c.heading_path) h where h ilike p_prefix || '%'
    );
$$;
