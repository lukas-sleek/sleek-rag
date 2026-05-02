-- Migration 0002: tables for Google Cloud RAG migration.
-- Old OpenAI columns remain for now — plan 13 drops them after cutover.

create extension if not exists vector;

-- ---------- New columns on existing tables ----------

alter table public.project_files
  add column if not exists gcs_blob_path text,        -- Supabase Storage path of original file
  add column if not exists mime_type text,
  add column if not exists page_count int,
  add column if not exists chunk_count int default 0,
  add column if not exists ingest_error text;

-- status enum stays text; we add new states: 'uploading','parsing','embedding','ready','failed'.
-- (Existing values 'pending','indexed','failed' are kept; ingestion writes the new ones.)

-- ---------- document_chunks ----------

create table public.document_chunks (
  id uuid primary key default gen_random_uuid(),
  file_id uuid not null references public.project_files(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,

  chunk_index int not null,                            -- order within the file
  block_type text not null,                            -- 'paragraph','heading','figure','table','list_item'
  content text not null,                               -- chunk text (figure caption for FIGURE blocks)
  page_start int not null,
  page_end int not null,
  heading_path text[],                                 -- e.g. ['3 Installation','3.6 Hydraulik']
  figure_label text,                                   -- e.g. 'Figure 3.6' (null if not a figure)

  embedding vector(768) not null,
  metadata jsonb default '{}'::jsonb,

  created_at timestamptz default now()
);

create index document_chunks_embedding_hnsw
  on public.document_chunks using hnsw (embedding vector_cosine_ops);

create index document_chunks_project_idx on public.document_chunks (project_id);
create index document_chunks_file_idx on public.document_chunks (file_id);
create index document_chunks_page_idx on public.document_chunks (file_id, page_start);
create index document_chunks_figure_idx on public.document_chunks (file_id, figure_label)
  where figure_label is not null;

-- ---------- chunk_images ----------

create table public.chunk_images (
  id uuid primary key default gen_random_uuid(),
  chunk_id uuid not null references public.document_chunks(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,

  storage_path text not null,                          -- 'chunk-images/{user_id}/{file_id}/{chunk_id}.png'
  caption text,                                        -- Gemini-generated description from Layout Parser
  width int, height int,
  byte_size int,

  created_at timestamptz default now()
);

create index chunk_images_chunk_idx on public.chunk_images (chunk_id);

-- ---------- chat_messages ----------
-- Replaces OpenAI Conversations API. RLS-scoped, server-stored history.

create table public.chat_messages (
  id uuid primary key default gen_random_uuid(),
  chat_id uuid not null references public.chats(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,

  role text not null check (role in ('user','assistant','tool')),
  content text not null,
  citations jsonb,                                     -- [{ chunk_id, file_id, filename, page_start, page_end, snippet, image_path? }]
  tool_name text,                                      -- non-null for tool messages (projektanalyse)

  created_at timestamptz default now()
);

create index chat_messages_chat_idx on public.chat_messages (chat_id, created_at);

-- ---------- RLS ----------

alter table public.document_chunks enable row level security;
alter table public.chunk_images   enable row level security;
alter table public.chat_messages  enable row level security;

create policy "owner full access" on public.document_chunks
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "owner full access" on public.chunk_images
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "owner full access" on public.chat_messages
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- ---------- Realtime publication ----------
-- Frontend subscribes to project_files updates for ingestion progress.

alter publication supabase_realtime add table public.project_files;
