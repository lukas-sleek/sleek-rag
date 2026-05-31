# Session findings — 2026-05-31 (index)

One file per finding. Covers the multi-Vorlagen feature, the "stuck LÄUFT"
activity-panel bug, the Gemini 3.1 grounding-metadata problem, and the
console-noise toggle. Written so a future session can pick any thread back up
without re-deriving it.

| # | Finding | File |
|---|---------|------|
| 1 | Multi-Vorlagen feature (up to 4 templates) — shipped | [1-multi-vorlagen.md](1-multi-vorlagen.md) |
| 2 | Activity-panel "stuck LÄUFT" bug — fixed | [2-activity-panel-laeuft.md](2-activity-panel-laeuft.md) |
| 3 | **Gemini 3.1 + serverless RAG: grounding_metadata intermittently None** — accepted | [3-gemini31-grounding.md](3-gemini31-grounding.md) |
| 4 | Console noise toggle (`VERBOSE_LOGS`) | [4-console-verbose-logs.md](4-console-verbose-logs.md) |
| 5 | Logging config gap (why app log.info never prints) | [5-logging-config-gap.md](5-logging-config-gap.md) |
| 6 | Environment: inotify watch limit + dev/prod ports | [6-env-inotify-ports.md](6-env-inotify-ports.md) |

Related: plan `.agent/plans/20.0.multi-vorlagen.md`, `PROGRESS.md` (Plan 20.0).
