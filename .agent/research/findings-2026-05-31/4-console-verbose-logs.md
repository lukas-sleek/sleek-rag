# 4. Console noise toggle (`VERBOSE_LOGS`)

Added a startup guard at the top of `backend/app/main.py` (runs before google
libs import). **Suppressed by default; `VERBOSE_LOGS=1` switches everything back
on. Nothing was removed.**

Silences:
- `FutureWarning`/`DeprecationWarning`/`UserWarning` (the `google.api_core`
  Python-version flood + Vertex/ADK `[EXPERIMENTAL]` notices).
- Chatty loggers → `ERROR`: `google_genai`, `google.genai`, `google.api_core`,
  `google.adk`, `vertexai` — this kills the per-turn google-genai
  *"non-text parts in the response: ['function_call']"* warning.
- **authlib's deprecation line**: authlib *forces* it to print at import time,
  ignoring warning filters (verified even `-W ignore` can't stop it). Worked
  around by pre-importing `authlib.jose` inside a
  `warnings.catch_warnings(record=True)` block so the one-shot warning is
  swallowed (later imports are cached no-ops).

Left intentionally (not "debug"): uvicorn access logs; our own `log.info`
(`chat[...]: event …`) lines.

To debug: `VERBOSE_LOGS=1 npm run dev` (or set in `.env`).
