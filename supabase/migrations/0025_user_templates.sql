-- Mehrere Vorlagen pro Nutzer (bis zu 4). Loest die 1-Zeile-pro-User-Tabelle
-- analysis_templates ab: jeder User kann jetzt bis zu 4 frei benannte Vorlagen
-- anlegen. Die bestehende Einzel-Vorlage wandert als position 0 / Name
-- 'Projektanalyse' herueber (kein Datenverlust).
--
-- analysis_templates bleibt vorerst read-only bestehen (Drop in 0026, nachdem
-- der Umstieg verifiziert ist).

create table public.user_templates (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users(id) on delete cascade,
  name       text not null,
  questions  text[] not null default '{}',
  position   smallint not null,            -- 0..3, Reihenfolge in UI/Picker
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, position)
);

alter table public.user_templates enable row level security;

create policy "owner full access" on public.user_templates
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

create index user_templates_user_idx on public.user_templates(user_id, position);

-- Harte Obergrenze von 4 Vorlagen pro Nutzer (Defence in depth; die API
-- erzwingt das zusaetzlich).
create or replace function public.enforce_max_user_templates()
returns trigger language plpgsql as $$
begin
  if (select count(*) from public.user_templates where user_id = new.user_id) >= 4 then
    raise exception 'Maximal 4 Vorlagen pro Nutzer erlaubt';
  end if;
  return new;
end;
$$;

create trigger user_templates_max_4
  before insert on public.user_templates
  for each row execute function public.enforce_max_user_templates();

-- Bestehende Einzel-Vorlagen migrieren -> position 0, Name 'Projektanalyse'.
insert into public.user_templates (user_id, name, questions, position)
  select user_id, 'Projektanalyse', questions, 0 from public.analysis_templates;

-- New-user seed: handle_new_user legt statt der analysis_templates-Zeile nun
-- eine user_templates-Zeile an (gleiche Default-Fragen, Name 'Projektanalyse').
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
declare
  fname text := coalesce(nullif(trim(new.raw_user_meta_data->>'first_name'), ''), null);
  pname text;
begin
  pname := case when fname is null then 'Mein Projekt' else fname || 's Projekt' end;
  insert into public.projects (user_id, name) values (new.id, pname);
  insert into public.user_templates (user_id, name, questions, position)
    values (new.id, 'Projektanalyse', public.default_analysis_questions(), 0);
  return new;
end;
$$;
