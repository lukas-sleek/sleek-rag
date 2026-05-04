-- Persist the activity-panel trace frames per assistant turn so the
-- collapsibles survive tab close / hard reload. chat_message_deltas remains
-- the append-only live stream + audit log; this column is the snapshot the
-- frontend reads on chat load.
alter table public.chat_messages
  add column if not exists traces jsonb;
