-- Migration 0006: ingest_jobs queue table for the async Document AI worker.

create table public.ingest_jobs (
  id uuid primary key default gen_random_uuid(),
  file_id uuid not null references public.project_files(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,

  state text not null default 'queued'
    check (state in ('queued','parsing','embedding','done','failed')),
  attempts int not null default 0,
  last_error text,

  docai_operation_name text,
  gcs_input_uri text,
  gcs_output_uri text,

  created_at timestamptz default now(),
  started_at timestamptz,
  finished_at timestamptz
);

create index ingest_jobs_state_idx on public.ingest_jobs (state, created_at)
  where state in ('queued','parsing');

alter table public.ingest_jobs enable row level security;

create policy "owner read" on public.ingest_jobs
  for select using (auth.uid() = user_id);
-- No insert/update/delete policy: only service role writes.
