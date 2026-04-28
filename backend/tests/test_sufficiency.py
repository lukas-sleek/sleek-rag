"""Plan 17.2 T6: Reasoning Agent / Sufficiency check.

Verifies:
  1. Empty chunks → sufficient=true (no autorater call needed).
  2. Sufficient verdict round-trips through Gemini's JSON response.
  3. Insufficient verdict surfaces `missing` and `feedback`.
  4. Gemini call failure → fail-open sufficient=true (never block answers).
  5. Non-JSON response → fail-open sufficient=true.
  6. build_continuation_hint includes missing + feedback when present.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import sufficiency as sufficiency_module
from app.retrieval import RetrievedChunk


def _mk_chunk(idx: int = 0) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"c{idx}",
        file_id="f-aaa",
        filename="doc.pdf",
        project_id="p1",
        content=f"content-{idx}",
        page_start=1,
        page_end=1,
        figure_label=None,
        block_type="paragraph",
        score=0.9,
    )


def _mock_gemini_response(payload_dict: dict | None, *, raises: Exception | None = None):
    """Build a fake gemini_client() that returns a chat completion with the
    given JSON payload as content. If `raises` is set, the create call
    raises instead."""
    fake_client = MagicMock()
    if raises is not None:
        fake_client.chat.completions.create.side_effect = raises
    else:
        msg = SimpleNamespace(
            content=(json.dumps(payload_dict) if payload_dict is not None else "")
        )
        fake_client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=msg)]
        )
    return fake_client


def test_empty_chunks_short_circuits_to_sufficient():
    out = sufficiency_module.assess_sufficiency(
        question="Welche Bauherren?", chunks=[]
    )
    assert out == {"sufficient": True, "missing": None, "feedback": None}


def test_sufficient_verdict(monkeypatch):
    fake_client = _mock_gemini_response(
        {"sufficient": True, "missing": None, "feedback": None}
    )
    monkeypatch.setattr(sufficiency_module, "gemini_client", lambda: fake_client)

    out = sufficiency_module.assess_sufficiency(
        question="Welche Bauherren?", chunks=[_mk_chunk()]
    )
    assert out["sufficient"] is True
    assert out["missing"] is None
    assert out["feedback"] is None


def test_insufficient_surfaces_missing_and_feedback(monkeypatch):
    fake_client = _mock_gemini_response(
        {
            "sufficient": False,
            "missing": "SBB, Manor und drei weitere Bauherren fehlen",
            "feedback": "list_document_outline auf Teil B aufrufen, "
            "dann read_section auf 'Beteiligte'",
        }
    )
    monkeypatch.setattr(sufficiency_module, "gemini_client", lambda: fake_client)

    out = sufficiency_module.assess_sufficiency(
        question="Welche Bauherren?", chunks=[_mk_chunk()]
    )
    assert out["sufficient"] is False
    assert "SBB" in out["missing"]
    assert "list_document_outline" in out["feedback"]


def test_gemini_failure_fails_open(monkeypatch):
    fake_client = _mock_gemini_response(None, raises=RuntimeError("boom"))
    monkeypatch.setattr(sufficiency_module, "gemini_client", lambda: fake_client)

    out = sufficiency_module.assess_sufficiency(
        question="x", chunks=[_mk_chunk()]
    )
    # Fail-open: rater unavailable must never block the answer.
    assert out == {"sufficient": True, "missing": None, "feedback": None}


def test_non_json_response_fails_open(monkeypatch):
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="hier ist kein JSON sorry")
            )
        ]
    )
    monkeypatch.setattr(sufficiency_module, "gemini_client", lambda: fake_client)

    out = sufficiency_module.assess_sufficiency(
        question="x", chunks=[_mk_chunk()]
    )
    assert out == {"sufficient": True, "missing": None, "feedback": None}


def test_continuation_hint_includes_missing_and_feedback():
    hint = sufficiency_module.build_continuation_hint(
        {
            "sufficient": False,
            "missing": "Bausumme-Total fehlt",
            "feedback": "read_section(Teil B, section='Grobkostenschätzung')",
        }
    )
    assert "SUFFICIENCY-CHECK" in hint
    assert "Bausumme-Total fehlt" in hint
    assert "Grobkostenschätzung" in hint
    assert "Tool-Aufruf" in hint  # nudges the agent to act


def test_continuation_hint_works_without_optional_fields():
    hint = sufficiency_module.build_continuation_hint(
        {"sufficient": False, "missing": None, "feedback": None}
    )
    assert "SUFFICIENCY-CHECK" in hint
