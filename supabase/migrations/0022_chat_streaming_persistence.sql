-- Persistent streaming for chat answers.
-- Goal: an in-flight assistant turn must survive tab close / chat switch.
--
-- chat_messages gains:
--   status  text  ('streaming'|'done'|'error')   default 'done' so existing rows stay correct
--   error   text                                  populated when status='error'
--
-- chat_message_deltas: append-only stream of payload chunks during generation.
-- The frontend subscribes via Supabase Realtime and reconstructs the in-flight
-- assistant turn. payload jsonb mirrors the existing SSE event shape so the
-- frontend dispatch logic stays the same (delta / trace / progress).

alter table public.chat_messages
  add column if not exists status text not null default 'done'
    check (status in ('streaming','done','error')),
  add column if not exists error text;

create index if not exists chat_messages_streaming_idx
  on public.chat_messages (chat_id)
  where status = 'streaming';

create table if not exists public.chat_message_deltas (
  id uuid primary key default gen_random_uuid(),
  message_id uuid not null references public.chat_messages(id) on delete cascade,
  chat_id uuid not null references public.chats(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  seq integer not null,
  payload jsonb not null,
  created_at timestamptz not null default now(),
  unique (message_id, seq)
);

create index if not exists chat_message_deltas_message_idx
  on public.chat_message_deltas (message_id, seq);

alter table public.chat_message_deltas enable row level security;

create policy "owner full access" on public.chat_message_deltas
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- Realtime: frontend needs both delta INSERTs and the final chat_messages
-- UPDATE (status -> done|error, content + citations populated).
alter publication supabase_realtime add table public.chat_messages;
alter publication supabase_realtime add table public.chat_message_deltas;

-- Realtime CDC needs full row data on UPDATE so the frontend gets content +
-- citations + status without an extra SELECT roundtrip.
alter table public.chat_messages replica identity full;
