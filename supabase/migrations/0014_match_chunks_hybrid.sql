-- Migration 0014: hybrid retrieval RPC.
--
-- match_chunks_hybrid replaces match_chunks_filtered as the primary retrieval
-- function for the agentic search_chunks tool. Pulls top-N candidates from
-- two channels in parallel — cosine over `embedding` and FTS over
-- `content_tsv` (German config) — and fuses ranks via Reciprocal Rank
-- Fusion (RRF, k=60). RRF is rank-based, so it sidesteps the cosine ↔
-- ts_rank score-calibration problem.
--
-- Filters are identical to match_chunks_filtered (file_ids, block_type,
-- page, figure_label, heading_prefix). The page-bucket tiebreaker from the
-- old RPC is preserved as the secondary sort so exact-page hits still beat
-- narrow-span at equal RRF score.
--
-- Empty p_query degrades cleanly to pure-vector via the coalesce() arms —
-- no separate code path needed; this is what RETRIEVAL_MODE=vector_only
-- relies on.

create or replace function public.match_chunks_hybrid(
  p_project_id uuid,
  p_embedding vector(768),
  p_query text,
  p_top_k int default 30,
  p_file_ids uuid[] default null,
  p_block_type text default null,
  p_page int default null,
  p_figure_label text default null,
  p_heading_prefix text default null,
  p_rrf_k int default 60
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
  vec_similarity float,
  fts_rank float,
  rrf_score float
) language sql stable as $$
  with
  filtered as (
    select c.id,
           c.file_id,
           c.project_id,
           c.content,
           c.page_start,
           c.page_end,
           c.figure_label,
           c.block_type,
           c.embedding,
           c.content_tsv,
           pf.filename
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
  ),
  vec as (
    select id,
           1 - (embedding <=> p_embedding) as sim,
           row_number() over (order by embedding <=> p_embedding) as rnk
    from filtered
    order by embedding <=> p_embedding
    limit greatest(1, coalesce(p_top_k, 30)) * 2
  ),
  fts as (
    select id,
           ts_rank_cd(content_tsv, plainto_tsquery('german', coalesce(p_query, ''))) as r,
           row_number() over (
             order by ts_rank_cd(
               content_tsv,
               plainto_tsquery('german', coalesce(p_query, ''))
             ) desc
           ) as rnk
    from filtered
    where coalesce(p_query, '') <> ''
      and content_tsv @@ plainto_tsquery('german', p_query)
    order by r desc
    limit greatest(1, coalesce(p_top_k, 30)) * 2
  ),
  fused as (
    select coalesce(v.id, f.id) as id,
           coalesce(v.sim, 0)   as vec_similarity,
           coalesce(f.r, 0)     as fts_rank,
           coalesce(1.0 / (p_rrf_k + v.rnk), 0)
             + coalesce(1.0 / (p_rrf_k + f.rnk), 0) as rrf_score
    from vec v
    full outer join fts f on f.id = v.id
  )
  select f.id,
         f.file_id,
         f.project_id,
         f.content,
         f.page_start,
         f.page_end,
         f.figure_label,
         f.block_type,
         f.filename,
         u.vec_similarity,
         u.fts_rank,
         u.rrf_score
  from fused u
  join filtered f on f.id = u.id
  order by u.rrf_score desc,
    case
      when p_page is null then 1
      when f.page_start = p_page and f.page_end = p_page then 0
      when (f.page_end - f.page_start) <= 1 then 1
      else 2
    end,
    (1 - (f.embedding <=> p_embedding)) desc
  limit greatest(1, least(coalesce(p_top_k, 30), 100));
$$;
