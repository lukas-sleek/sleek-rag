-- Per-project file deduplication by content checksum.
--
-- We hash the original uploaded bytes (pre-LibreOffice conversion) with
-- SHA-256 and store the lowercase hex digest. The partial unique index
-- enforces "the same content cannot exist twice in the same project",
-- independent of filename. Existing rows are NULL and excluded from the
-- constraint until a future backfill job populates them from GCS.

alter table public.project_files
  add column content_sha256 char(64) default null;

comment on column public.project_files.content_sha256 is
  'Lowercase hex SHA-256 of the original uploaded bytes (pre-conversion). '
  'Used for per-project content-addressed dedup. NULL on legacy rows that '
  'predate this column.';

create unique index if not exists project_files_project_content_sha256_uniq
  on public.project_files (project_id, content_sha256)
  where content_sha256 is not null;
