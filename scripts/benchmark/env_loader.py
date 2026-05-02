"""Shared env loading for benchmark scripts.

Reads the project-root .env (same file the FastAPI backend uses) and
points GOOGLE_APPLICATION_CREDENTIALS at the service-account JSON so
google-genai's vertexai=True path picks ADC up automatically.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[2]


def load_env() -> None:
    load_dotenv(_ROOT / ".env")
    sa_path = os.environ.get("GCP_SERVICE_ACCOUNT_JSON_PATH")
    if sa_path and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_path
