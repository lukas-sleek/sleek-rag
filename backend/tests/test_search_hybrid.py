"""Unit tests for execute_search_chunks (plan 16: hybrid + rerank + T6 query expansion).

Mocks the supabase RPC, the embeddings call, the file-id resolver, the
image-attach helper, and the ranking client. Verifies:
  1. hybrid mode pulls pre_rerank_k from RPC, then reranks down to top_k.
  2. rerank fail-open keeps RPC (RRF) order, trimmed to top_k.
  3. vector_only mode passes empty p_query to RPC and skips rerank.
  4. pre_rerank_k_override (Projektanalyse path) wins over settings.
  5. T6 query expansion: 'welche'-questions fan out to multiple RPC calls,
     RRF-merge, then rerank with original query.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import ranking_client
from app.config import settings
from app.tools import search as search_module


def _make_rpc_row(idx: int, *, prefix: str = "chunk") -> dict:
    return {
        "id": f"{prefix}-{idx}",
        "file_id": "file-aaaa",
        "project_id": "proj-1",
        "content": f"content-{idx}",
        "page_start": 1,
        "page_end": 1,
        "figure_label": None,
        "block_type": "paragraph",
        "filename": "f.pdf",
        "vec_similarity": 0.9 - (idx * 0.01),
        "fts_rank": 0.5,
        "rrf_score": 0.03,
    }


@pytest.fixture
def mocks(monkeypatch):
    captured: dict = {
        "rpc_calls": [],  # list[(name, params)]
        "rank_args": None,
    }

    rpc_rows_per_query: dict[str, list[dict]] = {}

    def set_rpc_rows(query_to_rows: dict[str, list[dict]]):
        rpc_rows_per_query.clear()
        rpc_rows_per_query.update(query_to_rows)

    captured["set_rpc_rows"] = set_rpc_rows

    default_rows = [_make_rpc_row(i) for i in range(30)]

    def fake_rpc(name, params):
        captured["rpc_calls"].append((name, dict(params)))
        rows = rpc_rows_per_query.get(params.get("p_query"), default_rows)
        return SimpleNamespace(execute=lambda r=rows: SimpleNamespace(data=r))

    fake_supabase = MagicMock()
    fake_supabase.rpc.side_effect = fake_rpc
    monkeypatch.setattr(search_module, "supabase", lambda: fake_supabase)

    def fake_embed_create(model, input, dimensions):
        if isinstance(input, str):
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[0.0] * dimensions)]
            )
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.0] * dimensions) for _ in input]
        )

    fake_client = SimpleNamespace(
        embeddings=SimpleNamespace(create=fake_embed_create),
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_kw: SimpleNamespace(
                    choices=[
                        SimpleNamespace(message=SimpleNamespace(content=""))
                    ]
                )
            )
        ),
    )
    monkeypatch.setattr(search_module, "gemini_client", lambda: fake_client)

    monkeypatch.setattr(search_module, "_attach_images", lambda chunks: chunks)
    monkeypatch.setattr(
        search_module, "resolve_file_id_prefixes", lambda *_a, **_k: []
    )

    def fake_rank(query, documents, top_n):
        captured["rank_args"] = {
            "query": query,
            "doc_count": len(documents),
            "top_n": top_n,
        }
        n = len(documents)
        # Reverse: last doc gets highest score.
        return [(n - 1 - i, 1.0 - 0.01 * i) for i in range(min(top_n, n))]

    monkeypatch.setattr(ranking_client, "rank", fake_rank)
    monkeypatch.setattr(search_module.ranking_client, "rank", fake_rank)

    monkeypatch.setattr(settings, "query_expansion", False, raising=False)

    return captured


def _last_rpc(captured):
    return captured["rpc_calls"][-1]


def test_hybrid_mode_reranks_to_top_k(mocks, monkeypatch):
    monkeypatch.setattr(settings, "retrieval_mode", "hybrid", raising=False)
    monkeypatch.setattr(settings, "pre_rerank_k", 30, raising=False)

    out = search_module.execute_search_chunks(
        args={"query": "Bauherr", "top_k": 5},
        project_id="proj-1",
        user_id="user-1",
    )

    name, params = _last_rpc(mocks)
    assert name == "match_chunks_hybrid"
    assert params["p_query"] == "Bauherr"
    assert params["p_top_k"] == 30
    assert mocks["rank_args"]["doc_count"] == 30
    assert mocks["rank_args"]["top_n"] == 5
    assert len(out["results"]) == 5
    assert out["results"][0]["chunk_id"] == "chunk-29"
    assert [r["ref"] for r in out["results"]] == [1, 2, 3, 4, 5]


def test_hybrid_rerank_fail_open_keeps_rrf_order(mocks, monkeypatch):
    monkeypatch.setattr(settings, "retrieval_mode", "hybrid", raising=False)
    monkeypatch.setattr(settings, "pre_rerank_k", 30, raising=False)

    monkeypatch.setattr(
        search_module.ranking_client,
        "rank",
        lambda **_kw: [(i, 0.0) for i in range(5)],
    )

    out = search_module.execute_search_chunks(
        args={"query": "test", "top_k": 5},
        project_id="proj-1",
        user_id="user-1",
    )

    assert [r["chunk_id"] for r in out["results"]] == [
        "chunk-0",
        "chunk-1",
        "chunk-2",
        "chunk-3",
        "chunk-4",
    ]


def test_vector_only_mode_skips_rerank(mocks, monkeypatch):
    monkeypatch.setattr(settings, "retrieval_mode", "vector_only", raising=False)

    out = search_module.execute_search_chunks(
        args={"query": "x", "top_k": 4},
        project_id="proj-1",
        user_id="user-1",
    )

    name, params = _last_rpc(mocks)
    assert name == "match_chunks_hybrid"
    assert params["p_query"] == ""
    assert params["p_top_k"] == 4
    assert mocks["rank_args"] is None
    assert [r["chunk_id"] for r in out["results"]] == [
        "chunk-0",
        "chunk-1",
        "chunk-2",
        "chunk-3",
    ]


def test_pre_rerank_k_override_wins(mocks, monkeypatch):
    monkeypatch.setattr(settings, "retrieval_mode", "hybrid", raising=False)
    monkeypatch.setattr(settings, "pre_rerank_k", 30, raising=False)

    search_module.execute_search_chunks(
        args={"query": "q", "top_k": 5},
        project_id="proj-1",
        user_id="user-1",
        pre_rerank_k_override=80,
    )

    _, params = _last_rpc(mocks)
    assert params["p_top_k"] == 80


def test_missing_query_short_circuits():
    out = search_module.execute_search_chunks(
        args={"query": ""},
        project_id="proj-1",
        user_id="user-1",
    )
    assert out == {"results": [], "error": "missing query"}


# --- T6 query-expansion tests ---


def test_expansion_fans_out_and_rrf_merges(mocks, monkeypatch):
    monkeypatch.setattr(settings, "retrieval_mode", "hybrid", raising=False)
    monkeypatch.setattr(settings, "pre_rerank_k", 30, raising=False)
    monkeypatch.setattr(settings, "query_expansion", True, raising=False)

    monkeypatch.setattr(
        search_module,
        "_expand_query",
        lambda q: ["Grundeigentümer", "Auftraggeber"],
    )

    # Different RPC results per query so we can verify RRF merging by id.
    mocks["set_rpc_rows"](
        {
            "Welche Bauherren?": [
                _make_rpc_row(0, prefix="orig"),
                _make_rpc_row(1, prefix="orig"),
            ],
            "Grundeigentümer": [
                _make_rpc_row(1, prefix="orig"),  # collides → boosted
                _make_rpc_row(2, prefix="grund"),
            ],
            "Auftraggeber": [
                _make_rpc_row(0, prefix="auftr"),
            ],
        }
    )

    monkeypatch.setattr(
        search_module.ranking_client,
        "rank",
        lambda query, documents, top_n: [(i, 0.0) for i in range(len(documents))],
    )

    out = search_module.execute_search_chunks(
        args={"query": "Welche Bauherren?", "top_k": 8},
        project_id="proj-1",
        user_id="user-1",
    )

    rpc_queries = [params["p_query"] for _, params in mocks["rpc_calls"]]
    assert rpc_queries == ["Welche Bauherren?", "Grundeigentümer", "Auftraggeber"]

    # orig-1 is in both first two queries → highest RRF, must rank first.
    ids = [r["chunk_id"] for r in out["results"]]
    assert ids[0] == "orig-1"
    # All four distinct chunks surface in the merged pool.
    assert set(ids) == {"orig-0", "orig-1", "grund-2", "auftr-0"}


def test_expansion_skipped_with_structural_filter(mocks, monkeypatch):
    monkeypatch.setattr(settings, "retrieval_mode", "hybrid", raising=False)
    monkeypatch.setattr(settings, "query_expansion", True, raising=False)

    expand_calls = []
    monkeypatch.setattr(
        search_module,
        "_expand_query",
        lambda q: expand_calls.append(q) or ["never used"],
    )

    search_module.execute_search_chunks(
        args={"query": "Welche Bauherren?", "top_k": 5, "page": 7},
        project_id="proj-1",
        user_id="user-1",
    )

    # `page` filter pins to a known cluster — expansion is wasted recall.
    assert expand_calls == []
    assert len(mocks["rpc_calls"]) == 1


def test_expansion_skipped_for_long_query(mocks, monkeypatch):
    monkeypatch.setattr(settings, "retrieval_mode", "hybrid", raising=False)
    monkeypatch.setattr(settings, "query_expansion", True, raising=False)

    expand_calls = []
    monkeypatch.setattr(
        search_module,
        "_expand_query",
        lambda q: expand_calls.append(q) or [],
    )

    search_module.execute_search_chunks(
        args={
            "query": (
                "Welche konkreten technischen Bauherren-Anforderungen "
                "werden in dem Dokument festgelegt und wer trägt sie?"
            ),
            "top_k": 5,
        },
        project_id="proj-1",
        user_id="user-1",
    )

    # Long descriptive query → don't expand.
    assert expand_calls == []


def test_expansion_skipped_for_non_trigger_query(mocks, monkeypatch):
    monkeypatch.setattr(settings, "retrieval_mode", "hybrid", raising=False)
    monkeypatch.setattr(settings, "query_expansion", True, raising=False)

    expand_calls = []
    monkeypatch.setattr(
        search_module,
        "_expand_query",
        lambda q: expand_calls.append(q) or [],
    )

    # Neither 'welche/wer' interrogative NOR a domain-noun trigger.
    search_module.execute_search_chunks(
        args={"query": "Hallo, wie geht es dir?", "top_k": 5},
        project_id="proj-1",
        user_id="user-1",
    )

    assert expand_calls == []


def test_expansion_off_when_flag_disabled(mocks, monkeypatch):
    monkeypatch.setattr(settings, "retrieval_mode", "hybrid", raising=False)
    monkeypatch.setattr(settings, "query_expansion", False, raising=False)

    expand_calls = []
    monkeypatch.setattr(
        search_module,
        "_expand_query",
        lambda q: expand_calls.append(q) or ["never used"],
    )

    search_module.execute_search_chunks(
        args={"query": "Welche Bauherren?", "top_k": 5},
        project_id="proj-1",
        user_id="user-1",
    )

    assert expand_calls == []
    assert len(mocks["rpc_calls"]) == 1
