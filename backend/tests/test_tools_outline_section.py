"""Unit tests for the plan-17 navigation/read tools.

Mocks the supabase RPC, the file-id resolver, and the image-attach helper.
Verifies:
  1. list_document_outline resolves the prefix, calls `document_outline` with
     the right params, and shapes rows into a flat heading list.
  2. list_document_outline returns an error envelope when the prefix is
     unknown — no RPC call.
  3. read_section resolves the prefix, calls `chunks_in_range` with the
     right params (section / page_from / page_to), assigns sequential refs
     starting at ref_offset+1, and returns the same shape as search_chunks.
  4. read_section returns an empty results list when the prefix is unknown.
  5. read_section RPC failure → fail-open `{results: [], error: rpc_failed}`.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.tools import outline as outline_module
from app.tools import section as section_module


@pytest.fixture
def supabase_mock(monkeypatch):
    captured: dict = {"rpc_calls": []}
    rows_for: dict[str, list[dict]] = {}

    def set_rows(name: str, rows: list[dict]):
        rows_for[name] = rows

    captured["set_rows"] = set_rows

    def fake_rpc(name, params):
        captured["rpc_calls"].append((name, dict(params)))
        rows = rows_for.get(name, [])
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=rows))

    fake_supabase = MagicMock()
    fake_supabase.rpc.side_effect = fake_rpc

    monkeypatch.setattr(outline_module, "supabase", lambda: fake_supabase)
    monkeypatch.setattr(section_module, "supabase", lambda: fake_supabase)
    monkeypatch.setattr(
        section_module, "_attach_images", lambda chunks: chunks
    )

    return captured


def _resolved(monkeypatch, mapping: dict[str, list[str]]):
    def fake_resolve(prefixes, project_id, user_id):
        out: list[str] = []
        for p in prefixes:
            out.extend(mapping.get(p, []))
        return out

    monkeypatch.setattr(
        outline_module, "resolve_file_id_prefixes", fake_resolve
    )
    monkeypatch.setattr(
        section_module, "resolve_file_id_prefixes", fake_resolve
    )


def test_outline_resolves_and_flattens_rows(supabase_mock, monkeypatch):
    _resolved(monkeypatch, {"abcd1234": ["abcd1234-0000-0000-0000-000000000000"]})
    supabase_mock["set_rows"](
        "document_outline",
        [
            {
                "heading_path": ["1.3 FRAGEN"],
                "page_start": 2,
                "page_end": 4,
                "chunk_count": 1,
            },
            {
                "heading_path": ["Projektorganisation"],
                "page_start": 21,
                "page_end": 22,
                "chunk_count": 3,
            },
        ],
    )

    out = outline_module.list_document_outline_executor(
        args={"file_id": "abcd1234"},
        project_id="proj-1",
        user_id="user-1",
    )

    assert supabase_mock["rpc_calls"][0][0] == "document_outline"
    params = supabase_mock["rpc_calls"][0][1]
    assert params["p_file_id"] == "abcd1234-0000-0000-0000-000000000000"
    assert params["p_user_id"] == "user-1"
    assert out["file_id"] == "abcd1234"
    assert out["outline"][0] == {
        "heading": "1.3 FRAGEN",
        "page_start": 2,
        "page_end": 4,
        "chunk_count": 1,
    }
    assert out["outline"][1]["heading"] == "Projektorganisation"


def test_outline_unknown_prefix_skips_rpc(supabase_mock, monkeypatch):
    _resolved(monkeypatch, {})  # nothing resolves

    out = outline_module.list_document_outline_executor(
        args={"file_id": "deadbeef"},
        project_id="proj-1",
        user_id="user-1",
    )

    assert out["outline"] == []
    assert out["error"]["code"] == "unknown_file_id"
    assert out["error"]["argument"] == "file_id"
    assert supabase_mock["rpc_calls"] == []


def test_outline_missing_file_id():
    out = outline_module.list_document_outline_executor(
        args={},
        project_id="proj-1",
        user_id="user-1",
    )
    assert out["outline"] == []
    assert out["error"]["code"] == "missing_required_argument"
    assert out["error"]["argument"] == "file_id"


def _section_row(idx: int) -> dict:
    return {
        "id": f"chunk-{idx}",
        "file_id": "abcd1234-0000-0000-0000-000000000000",
        "project_id": "proj-1",
        "content": f"content-{idx}",
        "page_start": idx,
        "page_end": idx,
        "figure_label": None,
        "block_type": "paragraph",
        "filename": "Teil_B.pdf",
    }


def test_read_section_passes_filters_and_assigns_refs(
    supabase_mock, monkeypatch
):
    _resolved(monkeypatch, {"abcd1234": ["abcd1234-0000-0000-0000-000000000000"]})
    supabase_mock["set_rows"](
        "chunks_in_range", [_section_row(i) for i in range(3)]
    )

    out = section_module.read_section_executor(
        args={
            "file_id": "abcd1234",
            "section": "Projektorganisation",
            "page_from": 20,
            "page_to": 25,
        },
        project_id="proj-1",
        user_id="user-1",
        ref_offset=10,
    )

    assert supabase_mock["rpc_calls"][0][0] == "chunks_in_range"
    params = supabase_mock["rpc_calls"][0][1]
    assert params["p_file_id"] == "abcd1234-0000-0000-0000-000000000000"
    assert params["p_user_id"] == "user-1"
    assert params["p_heading_prefix"] == "Projektorganisation"
    assert params["p_page_from"] == 20
    assert params["p_page_to"] == 25
    assert params["p_limit"] == 20

    refs = [r["ref"] for r in out["results"]]
    assert refs == [11, 12, 13]
    assert [r["chunk_id"] for r in out["results"]] == [
        "chunk-0",
        "chunk-1",
        "chunk-2",
    ]
    # _chunks envelope is preserved for the chat agent loop's collector.
    assert len(out["_chunks"]) == 3


def test_read_section_unknown_prefix(supabase_mock, monkeypatch):
    _resolved(monkeypatch, {})

    out = section_module.read_section_executor(
        args={"file_id": "deadbeef"},
        project_id="proj-1",
        user_id="user-1",
    )

    assert out["results"] == []
    assert out["error"]["code"] == "unknown_file_id"
    assert out["error"]["argument"] == "file_id"
    assert supabase_mock["rpc_calls"] == []


def test_read_section_rpc_failure_fail_open(monkeypatch):
    monkeypatch.setattr(
        section_module,
        "resolve_file_id_prefixes",
        lambda *_a, **_k: ["abcd1234-0000-0000-0000-000000000000"],
    )

    fake_supabase = MagicMock()

    def boom(*_a, **_k):
        raise RuntimeError("nope")

    fake_supabase.rpc.side_effect = boom
    monkeypatch.setattr(section_module, "supabase", lambda: fake_supabase)

    out = section_module.read_section_executor(
        args={"file_id": "abcd1234"},
        project_id="proj-1",
        user_id="user-1",
    )

    assert out["results"] == []
    assert out["error"]["code"] == "rpc_failed"
