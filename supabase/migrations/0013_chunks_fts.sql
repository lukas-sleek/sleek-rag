-- Migration 0013: full-text search column + GIN index for hybrid retrieval.
--
-- Plan 16: pure-cosine retrieval misses synonym/lexical matches the user
-- expects (Bauherr ↔ Grundeigentümer, Drittprojekt ↔ Schnittstellenprojekt).
-- A BM25-style FTS channel run in parallel with cosine and fused via RRF
-- closes that gap. This migration provisions the FTS side.
--
-- Stored generated column on (content, heading_path) using Postgres' German
-- text search config. Heading hits get setweight('A'), body gets 'B' so a
-- match in the section title outranks a match in body prose at equal
-- frequency.
--
-- We wrap the expression in an IMMUTABLE SQL function. Postgres rejects the
-- raw setweight(to_tsvector('german', ...)) || setweight(...) form on a
-- generated column with "generation expression is not immutable" — the
-- wrapper carries the IMMUTABLE marker so the planner accepts it. The body
-- itself is composed of immutable calls (to_tsvector(regconfig, text),
-- setweight, concat).

create or replace function public.chunks_tsv(p_content text, p_heading text[])
returns tsvector
language sql
immutable
as $$
  select setweight(
           to_tsvector('german'::regconfig, coalesce(p_content, '')),
           'B'
         )
       || setweight(
            to_tsvector(
              'german'::regconfig,
              coalesce(array_to_string(p_heading, ' '), '')
            ),
            'A'
          );
$$;

alter table public.document_chunks
  add column content_tsv tsvector
  generated always as (public.chunks_tsv(content, heading_path)) stored;

create index document_chunks_content_tsv_gin
  on public.document_chunks
  using gin (content_tsv);

-- Sanity: every existing row now has a non-null tsvector.
do $$
declare
  null_count int;
begin
  select count(*) into null_count
  from public.document_chunks
  where content_tsv is null;

  if null_count > 0 then
    raise exception '0013_chunks_fts: % chunks still have NULL content_tsv', null_count;
  end if;
end $$;
