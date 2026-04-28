"""Vertex AI Ranking API client — semantic cross-encoder rerank for hybrid
retrieval (plan 16).

Authenticates via the same service-account JSON we already use for Document
AI + GCS (workers/ingest.py:_gcs). Adds one new IAM role
(roles/discoveryengine.viewer) and one enabled API
(discoveryengine.googleapis.com) — no new secret, no new SDK.

Fail-open: any error or timeout returns the input order with score=0.0 so
the chat path keeps working when the Ranking API is unreachable.
"""
from __future__ import annotations

import logging

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account
from langsmith import traceable

from app.config import settings

log = logging.getLogger(__name__)

_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def _bearer_token() -> str:
    """Mint a fresh access token from the service-account JSON.

    Per-call refresh is acceptable at our QPS (~1 call per chat turn / 11 per
    Projektanalyse run). Token TTL is 1h; if the rerank step shows up as
    hot-path-relevant in LangSmith, cache the credentials object module-level
    (same pattern as workers/ingest.py:_gcs_client).
    """
    creds = service_account.Credentials.from_service_account_file(
        settings.gcp_service_account_json_path, scopes=[_SCOPE]
    )
    creds.refresh(GoogleAuthRequest())
    return creds.token


def _fail_open(documents: list[str], top_n: int) -> list[tuple[int, float]]:
    return [(i, 0.0) for i in range(min(len(documents), max(0, top_n)))]


@traceable(run_type="tool", name="rank")
def rank(
    query: str,
    documents: list[str],
    top_n: int,
    *,
    timeout: float | None = None,
) -> list[tuple[int, float]]:
    """Return list of (original_index, relevance_score) sorted by score desc,
    length ≤ top_n. Fails open: returns the input order with score=0.0 on any
    error/timeout."""
    if not documents or top_n <= 0:
        return []
    if not query.strip():
        return _fail_open(documents, top_n)
    if not settings.gcp_project_id or not settings.gcp_service_account_json_path:
        log.warning("rank: GCP not configured, falling back to RRF order")
        return _fail_open(documents, top_n)

    effective_timeout = timeout if timeout is not None else settings.rerank_timeout_sec

    try:
        token = _bearer_token()
    except Exception as exc:
        log.warning("rank: failed to mint bearer token: %s", exc)
        return _fail_open(documents, top_n)

    url = (
        f"https://discoveryengine.googleapis.com/v1/projects/"
        f"{settings.gcp_project_id}/locations/global/rankingConfigs/"
        f"default_ranking_config:rank"
    )
    body = {
        "model": settings.rerank_model,
        "query": query,
        "records": [
            {"id": str(i), "content": doc} for i, doc in enumerate(documents)
        ],
        "topN": top_n,
        "ignoreRecordDetailsInResponse": True,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=effective_timeout) as client:
            resp = client.post(url, json=body, headers=headers)
    except Exception as exc:
        log.warning("rank: HTTP transport error: %s", exc)
        return _fail_open(documents, top_n)

    if resp.status_code != 200:
        log.warning(
            "rank: non-200 %s — %s",
            resp.status_code,
            resp.text[:300],
        )
        return _fail_open(documents, top_n)

    try:
        data = resp.json()
        records = data.get("records") or []
        scored: list[tuple[int, float]] = []
        for r in records:
            rid = r.get("id")
            score = r.get("score")
            if rid is None or score is None:
                continue
            try:
                idx = int(rid)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(documents):
                scored.append((idx, float(score)))
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:top_n]
    except Exception as exc:
        log.warning("rank: failed to parse response: %s", exc)
        return _fail_open(documents, top_n)
