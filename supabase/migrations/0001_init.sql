-- M1 schema: projects, chats, project_files. RLS scoped per-user.
-- Apply via Supabase Dashboard → SQL editor (paste + run).

create extension if not exists "pgcrypto";

create table public.projects (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  openai_vector_store_id text,
  created_at timestamptz default now()
);

create table public.chats (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  title text not null,
  openai_thread_id text,
  created_at timestamptz default now()
);

create table public.project_files (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  filename text not null,
  size_bytes bigint,
  openai_file_id text,
  status text default 'pending',
  created_at timestamptz default now()
);

alter table public.projects enable row level security;
alter table public.chats enable row level security;
alter table public.project_files enable row level security;

create policy "owner full access" on public.projects
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "owner full access" on public.chats
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "owner full access" on public.project_files
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- Seed a default project on signup so new users land with one project ready.
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.projects (user_id, name) values (new.id, 'My Project');
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
