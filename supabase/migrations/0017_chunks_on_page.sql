-- Migration 0017: page-neighbor expansion helper for read_section.
--
-- Plan 17.4.1 F8a: when retrieval surfaces sub-rows of a table without
-- the headline row, the agent needs to read every chunk on the same
-- page(s) — not just the heading-/section-filtered subset — so the
-- table headline row (which often lives in its own chunk) becomes
-- visible. This RPC backs `read_section(include_page_neighbors=true)`.
--
-- Returns the same column shape as `chunks_in_range` plus heading_path
-- and chunk_index (parallel to migration 0016 for match_chunks_hybrid)
-- so `_rpc_row_to_chunk` can read the same named keys without branching.
--
-- Ownership enforced via project_files.user_id join — RLS-friendly when
-- called from the service-role backend.

create or replace function public.chunks_on_page(
  p_file_id uuid,
  p_user_id uuid,
  p_page int
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
  heading_path text[],
  chunk_index int
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
         coalesce(c.heading_path, array[]::text[]) as heading_path,
         coalesce(c.chunk_index, 0) as chunk_index
  from public.document_chunks c
  join public.project_files pf on pf.id = c.file_id
  where c.file_id = p_file_id
    and pf.user_id = p_user_id
    and c.page_start <= p_page
    and c.page_end >= p_page
  order by c.chunk_index;
$$;
