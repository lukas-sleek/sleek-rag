"""Plan 17.4.1 G3+G5: chat-side system prompt guardrails.

Asserts the load-bearing rules added to CHAT_SYSTEM_PROMPT and to
projektanalyse.ANSWER_INSTRUCTIONS — pure content checks. The runtime
behavior is exercised in test_chat_force_tool.py / test_chat_multi_tool_loop.py.
"""
from __future__ import annotations

from app.projektanalyse import ANSWER_INSTRUCTIONS
from app.routers.chats import CHAT_SYSTEM_PROMPT


def test_chat_prompt_contains_no_self_summing_rule():
    assert "NIEMALS Teilbeträge selbst summieren" in CHAT_SYSTEM_PROMPT
    assert "Total-/Summen-Fragen" in CHAT_SYSTEM_PROMPT


def test_chat_prompt_contains_role_policy_rule():
    """Tender-Projekt vor Auftragsvergabe wording is the load-bearing
    cue that lets the model surface client-side Projektleiter (Q3
    Kieliger) instead of refusing because the offerer side is unfilled."""
    assert "Tender-Projekt vor Auftragsvergabe" in CHAT_SYSTEM_PROMPT
    assert "Rollen-Familie" in CHAT_SYSTEM_PROMPT
    assert "Verweigere nur" in CHAT_SYSTEM_PROMPT


def test_projektanalyse_answer_instructions_carry_no_self_summing():
    """Parallel rule on the projektanalyse v1/v2 path so v1 rerun-from-
    chat doesn't bypass the chat-side guardrail."""
    assert "keine Selbst-Summierung" in ANSWER_INSTRUCTIONS
    assert "NIEMALS Teilbeträge selbst summieren" in ANSWER_INSTRUCTIONS


def test_projektanalyse_answer_instructions_carry_role_policy():
    assert "Tender-Projekt vor Auftragsvergabe" in ANSWER_INSTRUCTIONS
    assert "Rollen-Familie" in ANSWER_INSTRUCTIONS
