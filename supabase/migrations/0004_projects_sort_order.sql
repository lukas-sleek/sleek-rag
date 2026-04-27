-- Persist the user's drag-drop project ordering. Lower sort_order = higher
-- in the sidebar. New projects land at min(sort_order)-1 in create_project,
-- and a PUT /api/projects/order endpoint reassigns 0..n-1 after a drop.
-- Pre-existing rows (sort_order null) sort after explicit ones, falling
-- back to created_at desc.

alter table public.projects add column sort_order integer;
create index if not exists projects_user_sort_idx on public.projects (user_id, sort_order);
