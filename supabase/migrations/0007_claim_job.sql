-- Migration 0007: atomic claim function for the ingest worker.

create or replace function public.claim_next_ingest_job()
returns setof public.ingest_jobs
language plpgsql security definer as $$
declare
  job public.ingest_jobs;
begin
  select * into job from public.ingest_jobs
    where state = 'queued'
    order by created_at
    for update skip locked
    limit 1;
  if not found then return; end if;
  update public.ingest_jobs
    set state = 'parsing', attempts = attempts + 1, started_at = now()
    where id = job.id
    returning * into job;
  return next job;
end;
$$;

revoke all on function public.claim_next_ingest_job() from public, anon, authenticated;
