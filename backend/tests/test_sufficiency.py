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


def _mk_chunk(
    idx: int = 0,
    *,
    content: str | None = None,
    heading_path: list[str] | None = None,
    block_type: str = "paragraph",
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"c{idx}",
        file_id="abcd1234-0000-0000-0000-000000000000",
        filename="doc.pdf",
        project_id="p1",
        content=content if content is not None else f"content-{idx}",
        page_start=1,
        page_end=1,
        figure_label=None,
        block_type=block_type,
        score=0.9,
        heading_path=heading_path or [],
        chunk_index=idx,
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
    assert out == {
        "sufficient": True,
        "missing": None,
        "feedback": None,
        "question_type": None,
    }


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
    assert out == {
        "sufficient": True,
        "missing": None,
        "feedback": None,
        "question_type": None,
    }


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
    assert out == {
        "sufficient": True,
        "missing": None,
        "feedback": None,
        "question_type": None,
    }


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


# ---------------------------------------------------------------------------
# Plan 17.4 T1: rubric carries question-type rules; verdict round-trips.
# ---------------------------------------------------------------------------


def test_prompt_includes_question_type_rubric():
    """The prompt must enumerate all five question types and apply
    type-specific rules — load-bearing change for plan 17.4."""
    prompt = sufficiency_module._INSTRUCTION
    for qtype in ("point", "aggregation", "total", "phrase", "out_of_scope"):
        assert qtype in prompt, f"missing question_type {qtype!r} in rater prompt"
    # Aggregation must require ≥3 distinct entities or explicit "only X exists".
    assert "≥3 distinkte" in prompt or ">=3 distinkte" in prompt
    # Total must require an explicit headline value.
    assert "HEADLINE-Wert" in prompt or "Total-Wert" in prompt
    # Old "lieber sufficient=true" bias must be gone.
    assert "lieber `sufficient=true`" not in prompt


def test_aggregation_with_one_entity_is_insufficient(monkeypatch):
    fake_client = _mock_gemini_response(
        {
            "question_type": "aggregation",
            "sufficient": False,
            "missing": "weitere Bauherren — nur Hochdorf belegt, "
            "Frage erwartet ≥3",
            "feedback": "list_document_outline auf Teil B",
        }
    )
    monkeypatch.setattr(sufficiency_module, "gemini_client", lambda: fake_client)
    out = sufficiency_module.assess_sufficiency(
        question="Welche Bauherren sind beteiligt?",
        chunks=[_mk_chunk(content="Bauherr: Hochdorf AG")],
    )
    assert out["sufficient"] is False
    assert "Hochdorf" in out["missing"]


def test_aggregation_with_three_entities_is_sufficient(monkeypatch):
    fake_client = _mock_gemini_response(
        {
            "question_type": "aggregation",
            "sufficient": True,
            "missing": None,
            "feedback": None,
        }
    )
    monkeypatch.setattr(sufficiency_module, "gemini_client", lambda: fake_client)
    out = sufficiency_module.assess_sufficiency(
        question="Welche Bauherren sind beteiligt?",
        chunks=[
            _mk_chunk(0, content="Bauherren: Hochdorf, SBB, Manor"),
            _mk_chunk(1),
            _mk_chunk(2),
        ],
    )
    assert out["sufficient"] is True


def test_total_with_only_subrows_is_insufficient(monkeypatch):
    fake_client = _mock_gemini_response(
        {
            "question_type": "total",
            "sufficient": False,
            "missing": "Gesamtsumme/Total Bausumme fehlt; nur Etappen-"
            "Subzeilen vorhanden",
            "feedback": "read_section auf '4 BAUKOSTEN' Headline",
        }
    )
    monkeypatch.setattr(sufficiency_module, "gemini_client", lambda: fake_client)
    chunks = [
        _mk_chunk(
            i,
            content=f"Etappe {i}: CHF {i * 1_000_000}",
            heading_path=["4 BAUKOSTEN", "4.2 Grobkostenschätzung"],
            block_type="table",
        )
        for i in range(1, 6)
    ]
    out = sufficiency_module.assess_sufficiency(
        question="Was ist die Bausumme?", chunks=chunks
    )
    assert out["sufficient"] is False
    assert "Total" in out["missing"] or "Gesamt" in out["missing"]


def test_total_with_headline_value_is_sufficient(monkeypatch):
    fake_client = _mock_gemini_response(
        {
            "question_type": "total",
            "sufficient": True,
            "missing": None,
            "feedback": None,
        }
    )
    monkeypatch.setattr(sufficiency_module, "gemini_client", lambda: fake_client)
    out = sufficiency_module.assess_sufficiency(
        question="Was ist die Bausumme?",
        chunks=[
            _mk_chunk(
                0,
                content="Total Bausumme: CHF 39'114'000",
                heading_path=["4 BAUKOSTEN"],
                block_type="paragraph",
            )
        ],
    )
    assert out["sufficient"] is True


def test_phrase_not_present_is_insufficient(monkeypatch):
    fake_client = _mock_gemini_response(
        {
            "question_type": "phrase",
            "sufficient": False,
            "missing": "exakte Phrase 'Ist in einer späteren Phase zu "
            "detaillieren' nicht in den Chunks belegt",
            "feedback": "list_document_outline auf weiteren Datei-Teilen",
        }
    )
    monkeypatch.setattr(sufficiency_module, "gemini_client", lambda: fake_client)
    out = sufficiency_module.assess_sufficiency(
        question="Steht 'Ist in einer späteren Phase zu detaillieren' in den Plänen?",
        chunks=[_mk_chunk(content="random unrelated content")],
    )
    assert out["sufficient"] is False


def test_out_of_scope_with_scope_fallback_is_sufficient(monkeypatch):
    fake_client = _mock_gemini_response(
        {
            "question_type": "out_of_scope",
            "sufficient": True,
            "missing": None,
            "feedback": None,
        }
    )
    monkeypatch.setattr(sufficiency_module, "gemini_client", lambda: fake_client)
    out = sufficiency_module.assess_sufficiency(
        question="Wie viele Stunden sind für die Ausführung (SIA 51) vorgesehen?",
        chunks=[
            _mk_chunk(
                content=(
                    "Der Auftragsumfang umfasst nur SIA-Phasen 21 "
                    "(Machbarkeit) und 31 (Vorprojekt)."
                )
            )
        ],
    )
    assert out["sufficient"] is True


# ---------------------------------------------------------------------------
# Plan 17.4 T2c: rater chunk renderer surfaces metadata.
# ---------------------------------------------------------------------------


def test_chunk_renderer_includes_metadata_headers():
    chunk = _mk_chunk(
        content="Etappe 5: CHF 9'623'000",
        heading_path=[
            "4 BAUKOSTEN",
            "4.2 Grobkostenschätzung",
            "Tabelle 2",
        ],
        block_type="table",
    )
    rendered = sufficiency_module._format_chunks_for_rater([chunk])
    assert "file_id: abcd1234" in rendered
    assert "block_type: table" in rendered
    assert "heading_path: 4 BAUKOSTEN > 4.2 Grobkostenschätzung > Tabelle 2" in rendered
    assert "Etappe 5: CHF 9'623'000" in rendered


def test_chunk_renderer_handles_empty_heading_path():
    chunk = _mk_chunk(content="legacy content", heading_path=[])
    rendered = sufficiency_module._format_chunks_for_rater([chunk])
    assert "heading_path: -" in rendered
    assert "legacy content" in rendered


def test_chunk_renderer_clips_heading_path_to_four_levels():
    chunk = _mk_chunk(
        heading_path=["L1", "L2", "L3", "L4", "L5", "L6"],
    )
    rendered = sufficiency_module._format_chunks_for_rater([chunk])
    assert "L1 > L2 > L3 > L4" in rendered
    assert "L5" not in rendered


# ---------------------------------------------------------------------------
# Plan 17.4.1 G1: rater rubric encodes hard structural cues for the
# question-type classification step.
# ---------------------------------------------------------------------------


def test_prompt_includes_cue_rules_for_question_type_classification():
    prompt = sufficiency_module._INSTRUCTION
    # All four CUE blocks must be present in the rubric.
    for cue in ("CUE-A", "CUE-B", "CUE-C", "CUE-D"):
        assert cue in prompt, f"missing {cue} in rater prompt"
    # CUE-A: plural-noun aggregation (Bauherren / Termine / Drittprojekte).
    assert "welche Bauherren" in prompt
    assert "welche Termine" in prompt
    # CUE-B: total-shaped questions (Bausumme / Gesamtkosten).
    assert "Bausumme" in prompt
    assert "Gesamtkosten" in prompt
    # CUE-C: phrase / verbatim search.
    assert "wörtlich erwähnt" in prompt
    # CUE-D: out-of-scope SIA phases.
    assert "SIA 51" in prompt or "SIA 32" in prompt
    # Disambiguation hint: CUE-D wins over CUE-A on Ausführungsprojekt.
    assert "Ausführungsprojekt" in prompt


# ---------------------------------------------------------------------------
# Plan 17.4.1 G2: rater rubric carries fail-state examples for total-shaped
# questions so the rater stops over-greenlighting Q5-style sub-row chunks.
# ---------------------------------------------------------------------------


def test_prompt_includes_total_concrete_failure_examples():
    prompt = sufficiency_module._INSTRUCTION
    assert "KONKRETE BEISPIEL-FÄLLE" in prompt
    # Fall 1: explicit Bausumme sub-row example with include_page_neighbors.
    assert "include_page_neighbors=true" in prompt
    # Fall 4: same-table same-heading_path structural cue.
    assert "block_type=`table`" in prompt
    # Fall 3 (sufficient): explicit headline value example.
    assert "39'114'000" in prompt


def test_total_q5_subrow_pattern_yields_insufficient(monkeypatch):
    """Q5-shape: 3 sub-row chunks from the same table, no headline.
    Rater MUST return sufficient=false, and the feedback should reference
    `include_page_neighbors=true` so the agent retries with page expansion."""
    fake_client = _mock_gemini_response(
        {
            "question_type": "total",
            "sufficient": False,
            "missing": "Headline-Total-Zeile (Gesamt-Bausumme) fehlt; "
            "alle Chunks sind Sub-Zeilen derselben Tabelle.",
            "feedback": (
                "read_section(file_id=<X>, page_from=17, page_to=17, "
                "include_page_neighbors=true) um die Tabellen-Headline "
                "zu erfassen."
            ),
        }
    )
    monkeypatch.setattr(sufficiency_module, "gemini_client", lambda: fake_client)
    chunks = [
        _mk_chunk(
            i,
            content=f"Etappe {i}: CHF {i * 1_000_000}",
            heading_path=["4 BAUKOSTEN", "Tabelle 2"],
            block_type="table",
        )
        for i in range(1, 4)
    ]
    out = sufficiency_module.assess_sufficiency(
        question="Was ist die Bausumme?", chunks=chunks
    )
    assert out["sufficient"] is False
    assert "include_page_neighbors" in (out["feedback"] or "")


# ---------------------------------------------------------------------------
# Plan 17.4.1 G4: SufficiencyVerdict surfaces question_type so the
# answer-correctness verifier can skip phrase / out_of_scope turns.
# ---------------------------------------------------------------------------


def test_verdict_surfaces_question_type(monkeypatch):
    fake_client = _mock_gemini_response(
        {
            "question_type": "aggregation",
            "sufficient": True,
            "missing": None,
            "feedback": None,
        }
    )
    monkeypatch.setattr(sufficiency_module, "gemini_client", lambda: fake_client)
    out = sufficiency_module.assess_sufficiency(
        question="Welche Termine?",
        chunks=[_mk_chunk()],
    )
    assert out["question_type"] == "aggregation"


def test_empty_chunks_surfaces_question_type_none():
    out = sufficiency_module.assess_sufficiency(
        question="Hi", chunks=[]
    )
    assert out["question_type"] is None
