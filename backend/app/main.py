import asyncio
import logging
import os
import warnings
from contextlib import asynccontextmanager

# --- Console noise control --------------------------------------------------
# By default the dev console is kept readable by silencing chatty third-party
# output: google-genai's per-turn "non-text parts in the response" warning,
# Vertex/ADK [EXPERIMENTAL] notices, authlib deprecation, and api_core's
# Python-version FutureWarnings. Nothing is removed — set VERBOSE_LOGS=1 to
# switch all of it back on. Must run before the google libs are imported
# (below, via app.config / app.routers) to catch import-time warnings.
if os.getenv("VERBOSE_LOGS", "").lower() not in ("1", "true", "yes"):
    for _cat in (FutureWarning, DeprecationWarning, UserWarning):
        warnings.filterwarnings("ignore", category=_cat)
    # authlib forces its deprecation warning to print at import time and ignores
    # warning filters, so pre-import the offending module inside a recording
    # context that swallows it (later imports are cached no-ops).
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("ignore")
        try:
            import authlib.jose  # noqa: F401
        except Exception:  # noqa: BLE001
            pass
    for _noisy in (
        "google_genai",
        "google.genai",
        "google.api_core",
        "google.adk",
        "vertexai",
    ):
        logging.getLogger(_noisy).setLevel(logging.ERROR)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import chats, files, projects, templates, transcribe
from app.workers.rag_lro_poller import run_poller

if settings.langsmith_api_key:
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    if settings.langsmith_project:
        os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
    if settings.langsmith_endpoint:
        os.environ.setdefault("LANGSMITH_ENDPOINT", settings.langsmith_endpoint)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(run_poller())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


app = FastAPI(title="sleek-rag backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(chats.router)
app.include_router(files.router)
app.include_router(templates.router)
app.include_router(transcribe.router)


@app.get("/health")
def health():
    return {"status": "ok"}
