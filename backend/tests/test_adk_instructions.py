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
        "NO-V2-ESCALATION",
        "PROJEKTANALYSE-TOOLS",
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
        "NO-V2-ESCALATION",
        "ASCII-Spelling",
    ]:
        assert clause in CHAT_ORCHESTRATOR_INSTRUCTION, f"missing clause: {clause}"


def test_chat_orchestrator_instruction_has_pronoun_resolution_examples():
    assert "Hans Mueller" in CHAT_ORCHESTRATOR_INSTRUCTION
    assert "Bausumme" in CHAT_ORCHESTRATOR_INSTRUCTION
