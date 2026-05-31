# 2. Activity-panel "stuck LÄUFT" bug (fixed)

**Symptom:** in the batch tree, a `search_project_documents` row under a
"Frage N" row spun `LÄUFT` forever even though the Frage row was `fertig` and the
answer was present.

**Invariant (verified in `components/agent-activity.tsx`):** a row renders
`LAEUFT` **iff** its `kind === "tool_call"`. A `tool_response` always renders
`fertig`. Rows pair/upsert by frame `id`.

**Root cause:** the batch tools emit per-question retrieval rows with different
id suffixes — `dispatch_rag_questions` → `-q{idx}`, `run_projektanalyse` →
`-pa{idx}`. The frontend re-parenting that nests a search row under its Frage
row only ran for `dispatch_rag_questions` and only matched `/-q(\d+)$/`. So for
**every Projektanalyse run** the real retrieval row never attached under its
Frage row → `injectPlaceholders` thought the Frage had no retrieval child →
injected a `kind:"tool_call"` placeholder that spins forever. Separately,
placeholders never resolved on completion, so a question with no grounding
(see finding 3) had no real row to replace it.

**Fix (frontend-only, `agent-activity.tsx`):**
1. Re-parent for **both** `dispatch_rag_questions` and `run_projektanalyse`,
   matching `-q{idx}` **and** `-pa{idx}` (`/-(?:q|pa)(\d+)$/`).
2. `buildTree(steps, streaming)` — the injected placeholder is only a live
   `tool_call` (spinner) while `streaming` and no terminal status; otherwise it
   settles to a terminal `tool_response` with empty `chunks: []` → renders
   "Keine grundenden Treffer" instead of spinning.
