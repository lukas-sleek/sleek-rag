# 1. Multi-Vorlagen feature (shipped)

Generalised the single per-user Projektanalyse template into **up to 4 named
templates**. Full plan + task breakdown: `.agent/plans/20.0.multi-vorlagen.md`.
Per-plan log entry in `PROGRESS.md` (Plan 20.0).

Key shape:
- DB: `public.user_templates(id, user_id, name, questions text[], position 0..3,
  …)` with `unique(user_id, position)`, RLS owner-only, a 4-row cap trigger
  (`enforce_max_user_templates`). Migration `0025_user_templates.sql`.
- Old `analysis_templates` row migrated to `user_templates` position 0, name
  `Projektanalyse`. Old table kept as read-only backup.
- Backend: `routers/templates.py` → full CRUD (`GET/POST/PUT/DELETE
  /api/templates`, 409 at 4, smallest-free-slot positioning).
- ADK: `run_projektanalyse(template_name?)` resolves by name (exact →
  substring → disambiguation notice); orchestrator passes the bare name.
- Frontend: `template-modal.tsx` is a manager with **auto-save** (debounced
  ~700ms + flush on close; empty new drafts are never persisted = "ignore
  empty"); empty templates filtered out of the composer picker
  (`usableTemplates` in `app-shell.tsx`). Delete uses an in-app confirm dialog
  (not `window.confirm`).

## Production-safety note (shared DB)
The "RAG" Supabase project (`gbnpzuoidkzuahggujwu`) is **shared with
production**, which still runs the old code that reads `analysis_templates`.
Migration ordering used to stay safe:
- `0025` — new table + migrate data + `handle_new_user` seeds `user_templates`.
- `0026_handle_new_user_dual_write.sql` — **APPLIED**: `handle_new_user` seeds
  *both* tables so new prod signups don't break the old read path.
- `0027_drop_analysis_templates.sql` — **written but NOT applied**: reverts the
  dual-write and drops `analysis_templates`. Apply only after prod is on the new
  code.
