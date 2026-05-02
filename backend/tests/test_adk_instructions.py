"""Plan 19.0 T4 + T7 string-presence assertions on ADK instruction strings."""
from __future__ import annotations

from app.adk.instructions import CHAT_ORCHESTRATOR_INSTRUCTION, RAG_SPECIALIST_INSTRUCTION


def test_rag_specialist_instruction_contains_required_clauses():
    for clause in [
        "INPUT-VERTRAG",
        "NO-SELF-SUM",
        "SCOPE-FALLBACK",
        "ROLLEN-FRAGEN",
        "HONESTY",
        "ZITATION",
    ]:
        assert clause in RAG_SPECIALIST_INSTRUCTION, f"missing clause: {clause}"


def test_rag_specialist_instruction_drops_orchestrator_clauses():
    for forbidden in [
        "SMALLTALK",
        "PROJEKTANALYSE-VORLAGE",
    ]:
        assert forbidden not in RAG_SPECIALIST_INSTRUCTION, (
            f"unexpected clause: {forbidden}"
        )


def test_chat_orchestrator_instruction_contains_required_clauses():
    for clause in [
        "ROUTING-ENTSCHEIDUNG",
        "UMFORMULIERUNGS-REGELN",
        "PURE FOLGEFRAGE",
        "KONTEXT-ABHAENGIGE FOLGEFRAGE",
        "MEHRFACH-FRAGEN",
        "COMPOUND-FOLGEFRAGE",
        "WIEDERHOLTE 'NICHT ANGEGEBEN'",
        "PROJEKTANALYSE-VORLAGE",
        "ASCII-Spelling",
        "KONTEXT-INTELLIGENZ",
        "NO-SELF-DERIVATION",
    ]:
        assert clause in CHAT_ORCHESTRATOR_INSTRUCTION, f"missing clause: {clause}"


def test_chat_orchestrator_followup_uses_history_before_refusing():
    """Follow-ups must use chat history + rewrite to rag_specialist before any
    blanket refusal. The rule is generic — covers sums, counts, filters,
    comparisons, time spans, etc., not a specific question type."""
    txt = CHAT_ORCHESTRATOR_INSTRUCTION
    # Generic operations covered, not just sums
    for op in ["Summe", "Durchschnitt", "Differenz", "Anzahl", "Filterung",
               "Zeitspanne", "Min/Max"]:
        assert op in txt, f"missing generic operation example: {op}"
    # Three-step escalation: history → smart rewrite → labeled derivation
    assert "VERLAUF AUSWERTEN" in txt
    assert "SMART-REWRITE" in txt
    assert "DERIVATION AUS VERLAUF" in txt
    # Blanket refusals are explicitly forbidden
    assert "NIEMALS mit einer pauschalen Verweigerung" in txt
    # Derived value must be transparently labeled (not from documents)
    assert "NICHT direkt" in txt and "Dokumenten" in txt
    # Rewrite step must produce a *new* query, not repeat the previous one
    assert "WIEDERHOLE NICHT EINFACH die alte Anfrage" in txt


def test_chat_orchestrator_instruction_has_pronoun_resolution_examples():
    assert "Hans Mueller" in CHAT_ORCHESTRATOR_INSTRUCTION
    assert "Bausumme" in CHAT_ORCHESTRATOR_INSTRUCTION
