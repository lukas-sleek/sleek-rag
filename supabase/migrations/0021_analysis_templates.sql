-- Per-user Projektanalyse-Vorlage. Eine Zeile pro User; questions ist eine
-- geordnete Liste der zu beantwortenden Fragen, die der Orchestrator als
-- Batch durch dispatch_rag_questions schickt, sobald der User eine
-- Projektanalyse anfordert.
--
-- Default-Fragen werden a) per Trigger fuer neue User geseedet und b) per
-- Backfill in dieser Migration fuer bestehende User angelegt.

create table public.analysis_templates (
  user_id uuid primary key references auth.users(id) on delete cascade,
  questions text[] not null,
  updated_at timestamptz not null default now()
);

alter table public.analysis_templates enable row level security;

create policy "owner full access" on public.analysis_templates
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

create or replace function public.default_analysis_questions()
returns text[] language sql immutable as $$
  select array[
    'In welcher Phase werden Ingenieurdienstleitungen angefragt?',
    'Welche Bauherren sind beteiligt?',
    'Wie heisst der Projektleiter?',
    'Welche Termine sind vorgesehen? Gibt es zwingende Meilensteine fuer z.B. Zwischentermine, Gleisschlagwochenenden oder aehnliche?',
    'Was ist die Bausumme?',
    'Welche Drittprojekte tangieren den Perimeter?',
    'Welche Rahmenbedingungen betreffen das Projekt hinsichtlich Termine, Bauzeit oder aehnlichem?',
    'Welche Elemente sind vom Bauprojekt zu ueberarbeiten? Wie viel Stunden sind dafuer in der Ausschreibung vorgesehen?',
    'Welche Elemente sind im Ausfuehrungsprojekt zu ueberarbeiten oder zu aendern?',
    'Ist die Vermessung Bestandteil unseres Auftrags oder ist diese nur zu koordinieren?',
    'Steht in den Plaenen irgendwo der Kommentar "Ist in einer spaeteren Phase zu Detaillieren." oder etwas aehnliches?'
  ];
$$;

-- handle_new_user() um das Template-Seeding erweitern. Bestehende Project-
-- Insert-Logik aus 0002_first_name_seed.sql bleibt erhalten.
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
  insert into public.analysis_templates (user_id, questions)
    values (new.id, public.default_analysis_questions());
  return new;
end;
$$;

-- Backfill bestehender User
insert into public.analysis_templates (user_id, questions)
  select id, public.default_analysis_questions() from auth.users
  on conflict (user_id) do nothing;
