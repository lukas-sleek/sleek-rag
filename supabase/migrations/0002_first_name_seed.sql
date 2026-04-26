-- Seed the default project name from the user's first_name (set on signup
-- via supabase.auth.signUp options.data.first_name). Falls back to
-- 'Mein Projekt' if the metadata is missing.

create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
declare
  fname text := coalesce(nullif(trim(new.raw_user_meta_data->>'first_name'), ''), null);
  pname text;
begin
  if fname is null then
    pname := 'Mein Projekt';
  else
    pname := fname || 's Projekt';
  end if;
  insert into public.projects (user_id, name) values (new.id, pname);
  return new;
end;
$$;
