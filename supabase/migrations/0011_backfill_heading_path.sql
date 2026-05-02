-- Migration 0011: rebuild `heading_path[]` from chunk content.
--
-- The previous ingestion code (workers/ingest.py before plan 15) populated
-- `heading_path[]` from `chunk.page_headers`, which is the running PAGE
-- BANNER, not the ancestor heading chain. The actual hierarchy comes from
-- markdown heading lines that Document AI prepends to `chunk.content` when
-- `include_ancestor_headings=True` is set on the layout config.
--
-- This migration introduces a Postgres function that mirrors the Python
-- parser in `backend/app/ingest_headings.py`:
--   Path A — peel consecutive `#+ heading` lines from the top of content.
--   Path B — scan the remainder for inline section markers ("1.3 FRAGEN")
--            that Layout Parser failed to mark up as headings.
--
-- The function is then applied to every existing row in `document_chunks`
-- as a one-shot UPDATE — no Document AI re-call, no re-embed.

create or replace function public.extract_heading_path(p_content text)
returns text[]
language plpgsql
immutable
as $func$
declare
  result text[] := array[]::text[];
  lines text[];
  body text;
  m text[];
  entry text;
  i int;
  body_start int := 1;
  -- Mirrors _INLINE_SECTION_RE in app/ingest_headings.py.
  inline_pattern text :=
    '(?:^|(?<=\D))(\d+(?:\.\d+)+)\s+([A-ZÄÖÜ][A-ZÄÖÜ /-]{1,127}[A-ZÄÖÜ])(?=[A-ZÄÖÜ][a-zäöüß]|\W|$)';
begin
  if p_content is null or btrim(p_content) = '' then
    return null;
  end if;

  lines := string_to_array(p_content, E'\n');

  -- Path A: consume consecutive markdown heading lines from the top.
  for i in 1..coalesce(array_length(lines, 1), 0) loop
    m := regexp_match(lines[i], '^#+\s+(.+?)\s*$');
    if m is null then
      exit;
    end if;
    if length(btrim(m[1])) > 0 then
      result := array_append(result, btrim(m[1]));
    end if;
    body_start := i + 1;
  end loop;

  -- Body = remaining lines after the heading prefix.
  if body_start > coalesce(array_length(lines, 1), 0) then
    body := '';
  else
    body := array_to_string(lines[body_start:], E'\n');
  end if;

  -- Path B: scan the body for inline section markers, dedup against Path A.
  for m in select regexp_matches(body, inline_pattern, 'gm') loop
    entry := m[1] || ' ' || btrim(m[2]);
    if not (entry = any(result)) then
      result := array_append(result, entry);
    end if;
  end loop;

  if coalesce(array_length(result, 1), 0) = 0 then
    return null;
  end if;
  return result;
end;
$func$;

-- Sanity assertions — fail the migration if the function disagrees with the
-- Python parser on the cases pinned in tests/test_ingest_headings.py.
do $$
declare
  out text[];
begin
  -- Clean prepended chain (single).
  out := public.extract_heading_path(
    E'# 1.3 ZUSÄTZLICHE ANGABEN BEI BIETERGEMEINSCHAFTEN / SUBUNTERNEHMERN\n\nbody\n'
  );
  assert out = array['1.3 ZUSÄTZLICHE ANGABEN BEI BIETERGEMEINSCHAFTEN / SUBUNTERNEHMERN'],
    format('clean chain mismatch: %s', out);

  -- Multi-level chain.
  out := public.extract_heading_path(E'# A\n## 1\n### 1.1\n\nbody');
  assert out = array['A', '1', '1.1'], format('multi-level mismatch: %s', out);

  -- Run-on recovery case from Teil A.
  out := public.extract_heading_path(
    E'### 1.2 EINGABESTELLE\n\nPostadresse: 6280 Hochdorf1.3 FRAGENDie Fragen sind …\n'
  );
  assert '1.2 EINGABESTELLE' = any(out)
     and '1.3 FRAGEN' = any(out),
    format('run-on recovery mismatch: %s', out);

  -- Body-only chunk — null.
  assert public.extract_heading_path(
    E'Just some paragraph text with no heading and no markers.\n'
  ) is null,
    'body-only should be null';

  -- Empty / whitespace input — null.
  assert public.extract_heading_path(null) is null;
  assert public.extract_heading_path('') is null;
  assert public.extract_heading_path(E'   \n  \n') is null;

  -- False positives must be filtered.
  out := public.extract_heading_path(
    E'# Vorbemerkungen\n\nDer Auftraggeber hat page 1.3 mio CHF und Tel. 31.3 sec.\n'
  );
  assert out = array['Vorbemerkungen'], format('false-positive leak: %s', out);

  -- Dedup across Path A / Path B.
  out := public.extract_heading_path(
    E'# 1.3 FRAGEN\n\nWie in 1.3 FRAGEN beschrieben…\n'
  );
  assert out = array['1.3 FRAGEN'], format('dedup mismatch: %s', out);

  -- camelCase boundary inside run-on text.
  out := public.extract_heading_path(E'Vorher1.3 FRAGENDie Fragen folgen.');
  assert out = array['1.3 FRAGEN'], format('camelCase split mismatch: %s', out);

  -- Three-level inline marker.
  out := public.extract_heading_path(E'See section 3.4.2 ANFORDERUNGEN AN DAS BAUWERK.\n');
  assert out = array['3.4.2 ANFORDERUNGEN AN DAS BAUWERK'],
    format('three-level mismatch: %s', out);
end $$;

-- One-shot backfill: rewrite every existing row.
update public.document_chunks
   set heading_path = public.extract_heading_path(content);
