-- Cleanup nach dem Umstieg auf user_templates (Migrationen 0025 + 0026).
--
-- ACHTUNG: erst anwenden, NACHDEM Production auf den neuen Code deployt ist
-- (der user_templates statt analysis_templates liest). Bis dahin haelt das
-- Dual-Write aus 0026 beide Lesepfade gruen.
--
-- Reihenfolge ist wichtig: zuerst das analysis_templates-Insert aus
-- handle_new_user entfernen (sonst schlagen Neuanmeldungen nach dem Drop fehl),
-- dann die Tabelle droppen. default_analysis_questions() bleibt erhalten —
-- die Funktion wird weiter vom New-User-Seed und vom "Standardfragen
-- einfuegen"-Button genutzt.

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

drop table if exists public.analysis_templates;
