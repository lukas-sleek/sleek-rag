"""Plan 17.4.1 G4: answer-correctness verifier.

Verifies:
  1. Empty chunks → ok=true (no Gemini call).
  2. Empty draft → ok=true (no Gemini call).
  3. phrase question_type → ok=true (no Gemini call — coverage check
     handled by sufficiency).
  4. out_of_scope question_type → ok=true.
  5. Inversion (Q10-shape) returns ok=false with issue + fix; the
     correction-hint formatter surfaces both.
  6. Gemini call failure → fail-open ok=true.
  7. Non-JSON response → fail-open ok=true.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import answer_verifier as verifier_module
from app.retrieval import RetrievedChunk


def _mk_chunk(idx: int = 0, *, content: str = "content", block_type: str = "paragraph") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"c{idx}",
        file_id="abcd1234-0000-0000-0000-000000000000",
        filename="doc.pdf",
        project_id="p1",
        content=content,
        page_start=1,
        page_end=1,
        figure_label=None,
        block_type=block_type,
        score=0.9,
    )


def _mock_gemini_response(payload: dict | None, *, raises: Exception | None = None, raw: str | None = None):
    fake_client = MagicMock()
    if raises is not None:
        fake_client.chat.completions.create.side_effect = raises
        return fake_client
    if raw is not None:
        content = raw
    else:
        content = json.dumps(payload) if payload is not None else ""
    msg = SimpleNamespace(content=content)
    fake_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=msg)]
    )
    return fake_client


def test_empty_chunks_short_circuits_ok(monkeypatch):
    sentinel = MagicMock()
    monkeypatch.setattr(verifier_module, "gemini_client", lambda: sentinel)
    out = verifier_module.verify_answer(
        question="Q", draft="A", chunks=[], question_type="point"
    )
    assert out == {"ok": True, "issue": None, "fix": None}
    sentinel.chat.completions.create.assert_not_called()


def test_empty_draft_short_circuits_ok(monkeypatch):
    sentinel = MagicMock()
    monkeypatch.setattr(verifier_module, "gemini_client", lambda: sentinel)
    out = verifier_module.verify_answer(
        question="Q", draft="   ", chunks=[_mk_chunk()], question_type="point"
    )
    assert out == {"ok": True, "issue": None, "fix": None}
    sentinel.chat.completions.create.assert_not_called()


def test_phrase_type_skipped(monkeypatch):
    sentinel = MagicMock()
    monkeypatch.setattr(verifier_module, "gemini_client", lambda: sentinel)
    out = verifier_module.verify_answer(
        question='Steht "X" in den Plänen?',
        draft="Ja, X steht auf S.5",
        chunks=[_mk_chunk()],
        question_type="phrase",
    )
    assert out == {"ok": True, "issue": None, "fix": None}
    sentinel.chat.completions.create.assert_not_called()


def test_out_of_scope_type_skipped(monkeypatch):
    sentinel = MagicMock()
    monkeypatch.setattr(verifier_module, "gemini_client", lambda: sentinel)
    out = verifier_module.verify_answer(
        question="Wie viele Stunden für SIA 51?",
        draft="Nicht Teil der Beschaffung.",
        chunks=[_mk_chunk()],
        question_type="out_of_scope",
    )
    assert out == {"ok": True, "issue": None, "fix": None}
    sentinel.chat.completions.create.assert_not_called()


def test_inversion_q10_shape(monkeypatch):
    """Q10: chunks say 'Vermessung wird in separaten Mandaten vergeben',
    draft says 'Vermessung ist Teil des Auftrags' — verifier must flag."""
    fake = _mock_gemini_response(
        {
            "ok": False,
            "issue": "Entwurf sagt 'Teil des Auftrags', Chunks sagen "
            "'in separaten Mandaten zu vergeben'.",
            "fix": "Antworte: Vermessung ist NICHT Bestandteil des "
            "Auftrags, sondern in separaten Mandaten an Spezialisten "
            "vergeben — vom Anbieter nur zu koordinieren.",
        }
    )
    monkeypatch.setattr(verifier_module, "gemini_client", lambda: fake)

    chunks = [
        _mk_chunk(
            content=(
                "Die Vermessung ist in separaten Mandaten zu vergeben "
                "und vom Anbieter zu koordinieren."
            )
        )
    ]
    out = verifier_module.verify_answer(
        question="Ist Vermessung Bestandteil des Auftrags?",
        draft="Ja, Vermessung ist Teil des Auftrags des Anbieters.",
        chunks=chunks,
        question_type="point",
    )
    assert out["ok"] is False
    assert "separaten Mandaten" in (out["issue"] or "")
    assert "NICHT" in (out["fix"] or "")

    hint = verifier_module.build_verifier_correction_hint(out)
    assert "KORREKTHEITS-CHECK" in hint
    assert "separaten Mandaten" in hint
    assert "ZITAT-PFLICHT" in hint


def test_gemini_failure_fails_open(monkeypatch):
    fake = _mock_gemini_response(None, raises=RuntimeError("boom"))
    monkeypatch.setattr(verifier_module, "gemini_client", lambda: fake)
    out = verifier_module.verify_answer(
        question="Q",
        draft="A",
        chunks=[_mk_chunk()],
        question_type="point",
    )
    assert out == {"ok": True, "issue": None, "fix": None}


def test_non_json_response_fails_open(monkeypatch):
    fake = _mock_gemini_response(None, raw="kein JSON sorry")
    monkeypatch.setattr(verifier_module, "gemini_client", lambda: fake)
    out = verifier_module.verify_answer(
        question="Q",
        draft="A",
        chunks=[_mk_chunk()],
        question_type="point",
    )
    assert out == {"ok": True, "issue": None, "fix": None}


def test_aggregation_type_runs_verifier(monkeypatch):
    """aggregation must NOT be in the skip set — verifier should fire."""
    fake = _mock_gemini_response(
        {"ok": True, "issue": None, "fix": None}
    )
    monkeypatch.setattr(verifier_module, "gemini_client", lambda: fake)
    out = verifier_module.verify_answer(
        question="Welche Bauherren?",
        draft="SBB, Manor, Hochdorf.",
        chunks=[_mk_chunk()],
        question_type="aggregation",
    )
    assert out["ok"] is True
    fake.chat.completions.create.assert_called_once()
