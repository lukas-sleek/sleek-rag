"""Shared bootstrap for spike probes.

Loads env from repo root .env, initialises vertexai, and exposes a real
RAG corpus name + a service-account-backed credentials object that the
ADK Runner / AdkApp will pick up automatically.

Throwaway code; will be deleted with the spike directory after T0.
"""
from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

warnings.simplefilter("ignore")

# Make `app.*` importable.
REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

# Load env (.env at repo root).
from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

# Surface service-account creds to all google libraries before they init.
_sa_path = os.environ.get("GCP_SERVICE_ACCOUNT_JSON_PATH", "")
if _sa_path and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _sa_path

# Force Vertex AI mode for google-genai (the path AdkApp / ADK takes).
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", os.environ.get("GCP_PROJECT_ID", ""))
os.environ.setdefault(
    "GOOGLE_CLOUD_LOCATION", os.environ.get("GCP_LOCATION", "europe-west3")
)

import vertexai  # noqa: E402

vertexai.init(
    project=os.environ["GCP_PROJECT_ID"],
    location=os.environ.get("GCP_LOCATION", "europe-west3"),
)

# A real corpus seeded with documents — discovered via supabase query.
CORPUS_NAME = (
    "projects/1007445049099/locations/europe-west3/ragCorpora/3170534137668829184"
)
USER_ID = "spike-user"
