-- New users land with a "Playground" project and a default "Neuer Chat" so
-- the empty-state UI never shows the file-aware greeting before any chat
-- exists. Replaces the first_name-based name from 0002.

create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
declare
  proj_id uuid;
begin
  insert into public.projects (user_id, name)
  values (new.id, 'Playground')
  returning id into proj_id;

  insert into public.chats (project_id, user_id, title)
  values (proj_id, new.id, 'Neuer Chat');

  return new;
end;
$$;
