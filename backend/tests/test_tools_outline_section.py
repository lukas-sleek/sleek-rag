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


# ---------------------------------------------------------------------------
# Plan 17.4 T4: outline-first hard gate on read_section(section=...)
# ---------------------------------------------------------------------------


def test_read_section_section_without_outline_returns_envelope(
    supabase_mock, monkeypatch
):
    """`section` is set but the same file_id wasn't outlined this turn —
    must short-circuit with section_without_outline and skip the RPC."""
    _resolved(
        monkeypatch, {"abcd1234": ["abcd1234-0000-0000-0000-000000000000"]}
    )

    out = section_module.read_section_executor(
        args={"file_id": "abcd1234", "section": "Projektorganisation"},
        project_id="proj-1",
        user_id="user-1",
        outlined_file_ids=set(),
    )

    assert out["results"] == []
    assert out["_chunks"] == []
    assert out["error"]["code"] == "section_without_outline"
    assert out["error"]["argument"] == "section"
    # Guidance must steer the model to call list_document_outline first.
    assert "list_document_outline" in out["error"]["guidance"]
    # No RPC call made.
    assert supabase_mock["rpc_calls"] == []


def test_read_section_section_with_prior_outline_proceeds(
    supabase_mock, monkeypatch
):
    """Same `section` call passes when the file_id was already outlined."""
    full_id = "abcd1234-0000-0000-0000-000000000000"
    _resolved(monkeypatch, {"abcd1234": [full_id]})
    supabase_mock["set_rows"](
        "chunks_in_range", [_section_row(0)]
    )

    out = section_module.read_section_executor(
        args={"file_id": "abcd1234", "section": "Projektorganisation"},
        project_id="proj-1",
        user_id="user-1",
        outlined_file_ids={full_id},
    )

    assert "error" not in out
    assert len(out["results"]) == 1
    assert supabase_mock["rpc_calls"][0][0] == "chunks_in_range"


def test_read_section_page_only_bypasses_outline_gate(
    supabase_mock, monkeypatch
):
    """page_from/page_to without `section` must bypass the outline gate
    even when outlined_file_ids is empty — page-targeted reads don't
    depend on heading names."""
    _resolved(
        monkeypatch, {"abcd1234": ["abcd1234-0000-0000-0000-000000000000"]}
    )
    supabase_mock["set_rows"](
        "chunks_in_range", [_section_row(0)]
    )

    out = section_module.read_section_executor(
        args={"file_id": "abcd1234", "page_from": 1, "page_to": 2},
        project_id="proj-1",
        user_id="user-1",
        outlined_file_ids=set(),
    )

    assert "error" not in out
    assert len(out["results"]) == 1


def test_read_section_legacy_call_without_outlined_set_unchanged(
    supabase_mock, monkeypatch
):
    """When outlined_file_ids is None (legacy callers, batch paths), the
    gate is disabled — `section` is allowed without prior outline."""
    _resolved(
        monkeypatch, {"abcd1234": ["abcd1234-0000-0000-0000-000000000000"]}
    )
    supabase_mock["set_rows"](
        "chunks_in_range", [_section_row(0)]
    )

    out = section_module.read_section_executor(
        args={"file_id": "abcd1234", "section": "Projektorganisation"},
        project_id="proj-1",
        user_id="user-1",
        # outlined_file_ids omitted → defaults to None
    )

    assert "error" not in out
    assert len(out["results"]) == 1


# ---------------------------------------------------------------------------
# Plan 17.4.1 F8b: include_page_neighbors expands the chunk set page-wise.
# ---------------------------------------------------------------------------


def test_read_section_include_page_neighbors_expands(supabase_mock, monkeypatch):
    """include_page_neighbors=True calls chunks_on_page once per distinct
    page in the initial result, merges results in document order, and
    deduplicates by chunk id."""
    full_id = "abcd1234-0000-0000-0000-000000000000"
    _resolved(monkeypatch, {"abcd1234": [full_id]})

    initial = [
        {**_section_row(0), "page_start": 17, "page_end": 17},
        {**_section_row(1), "page_start": 18, "page_end": 18},
    ]
    page17_extra = {
        "id": "headline",
        "file_id": full_id,
        "project_id": "proj-1",
        "content": "Total Bausumme: CHF 39'114'000",
        "page_start": 17,
        "page_end": 17,
        "figure_label": None,
        "block_type": "paragraph",
        "filename": "Teil_B.pdf",
    }
    # chunks_on_page returns ALL chunks on the page (including the original).
    page_results = {
        17: [initial[0], page17_extra],
        18: [initial[1]],
    }

    def fake_rpc(name, params):
        supabase_mock["rpc_calls"].append((name, dict(params)))
        if name == "chunks_in_range":
            rows = initial
        elif name == "chunks_on_page":
            rows = page_results.get(params.get("p_page"), [])
        else:
            rows = []
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=rows))

    fake_supabase = MagicMock()
    fake_supabase.rpc.side_effect = fake_rpc
    monkeypatch.setattr(section_module, "supabase", lambda: fake_supabase)
    monkeypatch.setattr(section_module, "_attach_images", lambda chunks: chunks)

    out = section_module.read_section_executor(
        args={
            "file_id": "abcd1234",
            "page_from": 17,
            "page_to": 18,
            "include_page_neighbors": True,
        },
        project_id="proj-1",
        user_id="user-1",
    )

    rpc_names = [c[0] for c in supabase_mock["rpc_calls"]]
    # First the initial chunks_in_range, then chunks_on_page once per page.
    assert rpc_names[0] == "chunks_in_range"
    page_calls = [c for c in supabase_mock["rpc_calls"] if c[0] == "chunks_on_page"]
    assert len(page_calls) == 2
    assert {c[1]["p_page"] for c in page_calls} == {17, 18}

    chunk_ids = [r["chunk_id"] for r in out["results"]]
    assert "headline" in chunk_ids  # the previously-unranked headline row
    # No duplicates from the original chunks.
    assert len(chunk_ids) == len(set(chunk_ids))


def test_read_section_include_page_neighbors_default_false(
    supabase_mock, monkeypatch
):
    """Without the flag, chunks_on_page is never called (regression
    guard)."""
    _resolved(monkeypatch, {"abcd1234": ["abcd1234-0000-0000-0000-000000000000"]})
    supabase_mock["set_rows"]("chunks_in_range", [_section_row(0)])

    out = section_module.read_section_executor(
        args={"file_id": "abcd1234", "page_from": 1, "page_to": 1},
        project_id="proj-1",
        user_id="user-1",
    )
    rpc_names = [c[0] for c in supabase_mock["rpc_calls"]]
    assert "chunks_on_page" not in rpc_names
    assert len(out["results"]) == 1


def test_read_section_include_page_neighbors_empty_initial_no_expansion(
    supabase_mock, monkeypatch
):
    """If chunks_in_range returns nothing, page expansion is skipped — no
    pages to expand around."""
    _resolved(monkeypatch, {"abcd1234": ["abcd1234-0000-0000-0000-000000000000"]})
    supabase_mock["set_rows"]("chunks_in_range", [])

    out = section_module.read_section_executor(
        args={
            "file_id": "abcd1234",
            "page_from": 99,
            "page_to": 99,
            "include_page_neighbors": True,
        },
        project_id="proj-1",
        user_id="user-1",
    )
    rpc_names = [c[0] for c in supabase_mock["rpc_calls"]]
    assert "chunks_on_page" not in rpc_names
    assert out["results"] == []


def test_read_section_include_page_neighbors_respects_30_chunk_cap(
    supabase_mock, monkeypatch
):
    """Even when the page expansion overshoots, the 30-chunk hard cap
    applies."""
    full_id = "abcd1234-0000-0000-0000-000000000000"
    _resolved(monkeypatch, {"abcd1234": [full_id]})

    initial = [{**_section_row(0), "page_start": 5, "page_end": 5}]
    big_page = [
        {
            "id": f"big-{i}",
            "file_id": full_id,
            "project_id": "proj-1",
            "content": f"chunk {i}",
            "page_start": 5,
            "page_end": 5,
            "figure_label": None,
            "block_type": "paragraph",
            "filename": "Teil_B.pdf",
        }
        for i in range(50)
    ]

    def fake_rpc(name, params):
        supabase_mock["rpc_calls"].append((name, dict(params)))
        if name == "chunks_in_range":
            rows = initial
        elif name == "chunks_on_page":
            rows = big_page
        else:
            rows = []
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=rows))

    fake_supabase = MagicMock()
    fake_supabase.rpc.side_effect = fake_rpc
    monkeypatch.setattr(section_module, "supabase", lambda: fake_supabase)
    monkeypatch.setattr(section_module, "_attach_images", lambda chunks: chunks)

    out = section_module.read_section_executor(
        args={
            "file_id": "abcd1234",
            "page_from": 5,
            "page_to": 5,
            "include_page_neighbors": True,
        },
        project_id="proj-1",
        user_id="user-1",
    )
    assert len(out["results"]) == 30


def test_read_section_tool_schema_has_include_page_neighbors():
    """Schema must expose the boolean param so Gemini's function-calling
    pipeline picks it up."""
    params = section_module.READ_SECTION_TOOL["function"]["parameters"][
        "properties"
    ]
    assert "include_page_neighbors" in params
    assert params["include_page_neighbors"]["type"] == "boolean"


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
