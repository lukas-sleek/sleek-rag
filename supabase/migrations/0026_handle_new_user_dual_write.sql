-- Uebergangs-Sicherheitsnetz, solange Production noch den alten Code laeuft
-- (liest analysis_templates), Dev/neuer Code aber bereits user_templates.
--
-- handle_new_user seedet bei Neuanmeldungen BEIDE Tabellen, damit weder der
-- alte (analysis_templates) noch der neue (user_templates) Lesepfad bei neuen
-- Usern leer laeuft. Migration 0027 entfernt den analysis_templates-Write
-- wieder und droppt die Tabelle, sobald Production auf den neuen Code deployt
-- ist.

create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
declare
  fname text := coalesce(nullif(trim(new.raw_user_meta_data->>'first_name'), ''), null);
  pname text;
  defaults text[] := public.default_analysis_questions();
begin
  pname := case when fname is null then 'Mein Projekt' else fname || 's Projekt' end;
  insert into public.projects (user_id, name) values (new.id, pname);
  -- Neuer Lesepfad (Dev / kommender Prod-Code).
  insert into public.user_templates (user_id, name, questions, position)
    values (new.id, 'Projektanalyse', defaults, 0);
  -- Alter Lesepfad (aktuell noch live in Production).
  insert into public.analysis_templates (user_id, questions)
    values (new.id, defaults)
    on conflict (user_id) do nothing;
  return new;
end;
$$;
