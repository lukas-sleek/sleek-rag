-- Persist sidebar expand/collapse state per project. Defaults to collapsed;
-- create_project flips it to true for user-created projects, and the
-- frontend auto-expands the project containing the active chat on load
-- (also persisted) so reloading lands the user in the project they were
-- working in.

alter table public.projects add column expanded boolean not null default false;
