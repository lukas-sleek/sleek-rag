"""Per-corpus AdkApp factory (plan 19.0 T8b).

One AdkApp per project corpus, cached. Each AdkApp owns its own
InMemorySessionService — sessions do NOT cross instances under in-memory
storage (T0 probe 6). That is fine because strategy (c) replays Supabase
rows into a fresh per-turn session anyway. A migration to
VertexAiSessionService is a separate phase; when it lands, sessions will
share state across rebuilds via the external service.

`app_name` is hard-coded by AdkApp to "default-app-name" (T0 probe 7) —
not configurable via constructor.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict

from vertexai.preview.reasoning_engines import AdkApp

from app.rag_corpus import _init_vertex

from .agents import make_chat_orchestrator


_MAX_APPS = 256

_apps: "OrderedDict[str, AdkApp]" = OrderedDict()
_locks: dict[str, asyncio.Lock] = {}
_global_lock = asyncio.Lock()


async def get_or_build_app(corpus_name: str) -> AdkApp:
    # Hot path: cached.
    if corpus_name in _apps:
        _apps.move_to_end(corpus_name)
        return _apps[corpus_name]

    # Cold path: serialize per-corpus to avoid double-build.
    async with _global_lock:
        lock = _locks.setdefault(corpus_name, asyncio.Lock())

    async with lock:
        if corpus_name in _apps:
            _apps.move_to_end(corpus_name)
            return _apps[corpus_name]

        # AdkApp pulls project/location from vertexai's global initializer.
        await asyncio.to_thread(_init_vertex)
        app = AdkApp(agent=make_chat_orchestrator(corpus_name))
        # Force runner / session_service / artifact_service initialisation
        # so callers can rely on `_tmpl_attrs["session_service"]` being
        # present on the very first turn.
        app.set_up()
        _apps[corpus_name] = app
        _apps.move_to_end(corpus_name)

        if len(_apps) > _MAX_APPS:
            # Drop oldest. In-flight requests still hold a reference,
            # so eviction is GC-safe.
            _apps.popitem(last=False)

        return app
