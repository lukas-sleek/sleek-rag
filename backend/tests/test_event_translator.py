"""Unit tests for the event_translator helpers."""
from __future__ import annotations

from app.adk.event_translator import (
    event_author,
    event_kind,
    event_state_delta,
    event_text,
)


def _model_text(text, author="chat_orchestrator"):
    return {
        "author": author,
        "content": {"role": "model", "parts": [{"text": text}]},
    }


def _tool_call(name, args, author="chat_orchestrator"):
    return {
        "author": author,
        "content": {
            "role": "model",
            "parts": [{"function_call": {"id": "abc", "name": name, "args": args}}],
        },
    }


def _tool_response(name, response, author="chat_orchestrator"):
    return {
        "author": author,
        "content": {
            "role": "user",
            "parts": [{
                "function_response": {"id": "abc", "name": name, "response": response}
            }],
        },
    }


def test_kind_model_text():
    assert event_kind(_model_text("hello")) == "model_text"


def test_kind_tool_call():
    assert event_kind(_tool_call("dispatch_rag_questions", {})) == "tool_call"


def test_kind_tool_response():
    assert (
        event_kind(_tool_response("dispatch_rag_questions", {"answers": []}))
        == "tool_response"
    )


def test_kind_other_for_empty_event():
    assert event_kind({}) == "other"


def test_event_author():
    assert event_author(_model_text("x", author="rag_specialist")) == "rag_specialist"


def test_event_text_joins_parts():
    evt = {
        "author": "x",
        "content": {"role": "model", "parts": [{"text": "ab"}, {"text": "cd"}]},
    }
    assert event_text(evt) == "abcd"


def test_event_state_delta_default_empty():
    assert event_state_delta({}) == {}


def test_event_state_delta_present():
    evt = {"actions": {"state_delta": {"citations": [1, 2]}}}
    assert event_state_delta(evt) == {"citations": [1, 2]}
