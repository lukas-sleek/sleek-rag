"""Unit tests for ranking_client.rank — Vertex AI Ranking API wrapper.

Stubs out the bearer-token mint and the HTTP call so the tests stay
hermetic. Verifies the three contract guarantees:
  1. 200 OK with reordered records → indices reordered, scores preserved.
  2. 503 → fail-open, returns input order with score=0.0.
  3. Timeout / transport error → fail-open, same shape.
"""
from __future__ import annotations

import httpx
import pytest

from app import ranking_client


@pytest.fixture(autouse=True)
def _stub_creds(monkeypatch):
    monkeypatch.setattr(ranking_client, "_bearer_token", lambda: "fake-token")
    monkeypatch.setattr(
        ranking_client.settings, "gcp_project_id", "fake-project", raising=False
    )
    monkeypatch.setattr(
        ranking_client.settings,
        "gcp_service_account_json_path",
        "/tmp/fake-key.json",
        raising=False,
    )
    monkeypatch.setattr(
        ranking_client.settings,
        "rerank_model",
        "semantic-ranker-default-004",
        raising=False,
    )
    monkeypatch.setattr(
        ranking_client.settings, "rerank_timeout_sec", 1.0, raising=False
    )


def _patch_transport(monkeypatch, handler):
    """Replace httpx.Client with one wired to MockTransport(handler)."""
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(ranking_client.httpx, "Client", factory)


def test_rank_reorders_by_score(monkeypatch):
    docs = ["alpha doc", "beta doc", "gamma doc"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "records": [
                    {"id": "2", "score": 0.9},
                    {"id": "0", "score": 0.5},
                    {"id": "1", "score": 0.1},
                ]
            },
        )

    _patch_transport(monkeypatch, handler)
    result = ranking_client.rank("query", docs, top_n=2)
    assert result == [(2, pytest.approx(0.9)), (0, pytest.approx(0.5))]


def test_rank_503_fails_open(monkeypatch):
    docs = ["a", "b", "c"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "unavailable"})

    _patch_transport(monkeypatch, handler)
    result = ranking_client.rank("q", docs, top_n=2)
    assert result == [(0, 0.0), (1, 0.0)]


def test_rank_timeout_fails_open(monkeypatch):
    docs = ["a", "b"]

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated")

    _patch_transport(monkeypatch, handler)
    result = ranking_client.rank("q", docs, top_n=5)
    # top_n > len(docs): result clipped to len(docs).
    assert result == [(0, 0.0), (1, 0.0)]


def test_rank_empty_docs():
    assert ranking_client.rank("q", [], top_n=5) == []


def test_rank_zero_top_n():
    assert ranking_client.rank("q", ["x"], top_n=0) == []


def test_rank_blank_query_skips_call():
    # Empty query → fail-open without firing HTTP.
    result = ranking_client.rank("   ", ["x", "y"], top_n=2)
    assert result == [(0, 0.0), (1, 0.0)]
