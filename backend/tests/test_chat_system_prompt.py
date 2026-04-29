"""Plan 18.3 T5: SYSTEM_INSTRUCTION content checks.

Pure-text guardrails on the consolidated Pattern A system_instruction. The
runtime behavior they enforce is exercised in test_chat_pattern_a_stream.py
and the Task 10 manual smoke (incl. 5–10 adversarial prompts that probe
the no-v2-escalation clause).
"""
from __future__ import annotations

from app.system_instructions import SYSTEM_INSTRUCTION


def test_no_self_summing_clause_present():
    assert "NIEMALS Teilbeträge selbst summieren" in SYSTEM_INSTRUCTION


def test_role_policy_clause_present():
    """The Tender-Projekt-vor-Auftragsvergabe wording is the load-bearing
    cue that lets the model surface client-side Projektleiter (Q3 Kieliger)
    instead of refusing because the offerer side is unfilled."""
    assert "Tender-Projekt vor Auftragsvergabe" in SYSTEM_INSTRUCTION
    assert "Rollen-Familie" in SYSTEM_INSTRUCTION
    assert "Verweigere nur" in SYSTEM_INSTRUCTION


def test_scope_fallback_sia_21_31_clause_present():
    assert "SIA-Phasen 21" in SYSTEM_INSTRUCTION
    assert "BAUPROJEKT (SIA 32/41)" in SYSTEM_INSTRUCTION
    assert "Nicht Teil dieser Beschaffung" in SYSTEM_INSTRUCTION


def test_honesty_uncertainty_clause_present():
    """Replaces the deleted answer_verifier (master plan §"Domain rules
    that MUST survive" item 3)."""
    assert "HONESTY UND UNSICHERHEIT" in SYSTEM_INSTRUCTION
    assert "Erfinde keine Werte" in SYSTEM_INSTRUCTION
    assert "nicht angegeben" in SYSTEM_INSTRUCTION


def test_aggregation_clause_present():
    """Replaces the aggregation portion of the deleted sufficiency rater
    (master plan §"Domain rules that MUST survive" item 4)."""
    assert "Aggregations-" in SYSTEM_INSTRUCTION
    assert "mindestens N, weitere sind möglich" in SYSTEM_INSTRUCTION


def test_no_v2_escalation_clause_present():
    """Replaces the deleted force_tool_next_iter code-level guard. The
    no-v2 rule now lives entirely in this prompt — if the model violates
    it, it's a prompt-tightening issue, not a code issue (master plan
    §"Domain rules that MUST survive" item 1)."""
    assert "NO-V2-ESCALATION" in SYSTEM_INSTRUCTION
    assert "run_projektanalyse_v2" in SYSTEM_INSTRUCTION
    assert "user-elected" in SYSTEM_INSTRUCTION
    assert "NIEMALS proaktiv" in SYSTEM_INSTRUCTION


def test_projektanalyse_tool_passthrough_clause_present():
    """Tool result must be emitted verbatim — no summary, no commentary."""
    assert "run_projektanalyse" in SYSTEM_INSTRUCTION
    assert "exakt und vollständig" in SYSTEM_INSTRUCTION
