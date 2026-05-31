# 5. Logging config gap (good to know)

The backend has **no logging configuration anywhere** — no `basicConfig`,
no dictConfig. So the root logger defaults to `WARNING` and **every app-level
`log.info(...)` is silently dropped** (e.g. the `chat[...]: event kind=…` traces
in `chats.py` never print). That's why temporary diagnostics during this session
had to be logged at `log.warning` to be visible.

If you ever want app INFO traces in the console, add a
`logging.basicConfig(level=INFO)` (or a dictConfig) — but mind it would also
re-expose the library noise unless paired with the `VERBOSE_LOGS` suppression
(see finding 4).
