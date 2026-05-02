-- Migration 0010: unified retrieval RPC for the agentic `search_chunks` tool.
-- One function fans out to all four legacy branches (vector / page / figure /
-- heading) plus the optional file_ids and block_type narrowing. Always
-- ranked by cosine similarity against p_embedding so every result has a
-- real score (no more hardcoded 1.0 placeholders in LangSmith traces).
--
-- Page ranking rule:
--   exact-page (page_start == page_end == p_page)  -> bucket 0
--   narrow-span (page_end - page_start <= 1)        -> bucket 1
--   wide-span                                       -> bucket 2
-- Within each bucket, rows are sorted by cosine distance.

create or replace function public.match_chunks_filtered(
  p_project_id uuid,
  p_embedding vector(768),
  p_top_k int default 8,
  p_file_ids uuid[] default null,
  p_block_type text default null,
  p_page int default null,
  p_figure_label text default null,
  p_heading_prefix text default null
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
    and (p_file_ids is null or c.file_id = any(p_file_ids))
    and (p_block_type is null or c.block_type = p_block_type)
    and (
      p_page is null
      or (c.page_start <= p_page and c.page_end >= p_page)
    )
    and (p_figure_label is null or c.figure_label = p_figure_label)
    and (
      p_heading_prefix is null
      or (
        c.heading_path is not null
        and exists (
          select 1
          from unnest(c.heading_path) h
          where h ilike p_heading_prefix || '%'
        )
      )
    )
  order by
    case
      when p_page is null then 1
      when c.page_start = p_page and c.page_end = p_page then 0
      when (c.page_end - c.page_start) <= 1 then 1
      else 2
    end,
    c.embedding <=> p_embedding
  limit greatest(1, least(coalesce(p_top_k, 8), 20));
$$;
