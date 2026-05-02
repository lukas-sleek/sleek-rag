-- Migration 0015: read-side helpers for the deep-dive agent (plan 17).
--
-- `document_outline(file_id, user_id)` returns a compact, scannable heading
-- tree for one file: one row per distinct heading entry with its page span
-- and chunk count. Used by the new `list_document_outline` tool so the model
-- can see what's *in* a file before deciding where to dig.
--
-- `chunks_in_range(file_id, user_id, page_from, page_to, heading_prefix,
-- limit)` returns chunks of one file in document order — no retrieval
-- ranking. Hard-capped at 30 rows so a single call stays cheap (~5k tokens).
-- Used by the new `read_section` tool for targeted exhaustive reads when
-- retrieval rank misses small fact-bearing chunks (table headlines, figure
-- captions, single-line list items).
--
-- Both functions enforce ownership via the join to `project_files` so they
-- are RLS-friendly even when invoked from the service-role backend.

create or replace function public.document_outline(
  p_file_id uuid,
  p_user_id uuid
) returns table (
  heading_path text[],
  page_start int,
  page_end int,
  chunk_count int
) language sql stable as $$
  with expanded as (
    select unnest(c.heading_path) as heading,
           c.page_start,
           c.page_end,
           c.id
    from public.document_chunks c
    join public.project_files pf on pf.id = c.file_id
    where c.file_id = p_file_id
      and pf.user_id = p_user_id
      and c.heading_path is not null
  )
  select array[heading]::text[],
         min(page_start) as page_start,
         max(page_end) as page_end,
         count(distinct id)::int as chunk_count
  from expanded
  group by heading
  order by min(page_start), heading;
$$;


create or replace function public.chunks_in_range(
  p_file_id uuid,
  p_user_id uuid,
  p_page_from int default null,
  p_page_to int default null,
  p_heading_prefix text default null,
  p_limit int default 20
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
  where c.file_id = p_file_id
    and pf.user_id = p_user_id
    and (p_page_from is null or c.page_end >= p_page_from)
    and (p_page_to is null or c.page_start <= p_page_to)
    and (
      p_heading_prefix is null
      or (
        c.heading_path is not null
        and exists (
          select 1 from unnest(c.heading_path) h
          where h ilike p_heading_prefix || '%'
        )
      )
    )
  order by c.page_start, c.chunk_index
  limit greatest(1, least(coalesce(p_limit, 20), 30));
$$;
