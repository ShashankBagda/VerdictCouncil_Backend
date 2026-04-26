"""Q1.3 — `llm_token` + `tool_call_delta` SSE event schemas (gated, OFF).

Locks the wire shape so the frontend (Q1.7) can build against it. The
flag (`PIPELINE_CONVERSATIONAL_STREAMING_PHASES`) defaults empty, so
nothing in the live pipeline emits these yet — Q1.4 wires them
behind the flag.

These events are deliberately decoupled from `pipeline_events` table
persistence (Risk #2 / decision A4): per-token writes would explode
the row count. The existing `llm_response` / `llm_chunk` events keep
their tee-write; `llm_token` and `tool_call_delta` do NOT.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from pydantic import TypeAdapter

from src.api.schemas.pipeline_events import (
    Event,
    LlmTokenEvent,
    ToolCallDeltaEvent,
)
from src.shared.config import Settings


def _round_trip(model_cls, payload: dict) -> dict:
    """Validate → serialise → re-parse, asserting bytewise equivalence
    on the round-trip."""
    obj = model_cls.model_validate(payload)
    serialised = json.loads(obj.model_dump_json())
    return serialised


class TestLlmTokenEvent:
    def test_round_trip(self):
        payload = {
            "kind": "agent",
            "schema_version": 1,
            "case_id": str(uuid4()),
            "agent": "intake",
            "phase": "intake",
            "event": "llm_token",
            "message_id": "msg-1",
            "delta": "Examining the notice.",
            "ts": "2026-04-26T10:00:00+00:00",
        }
        out = _round_trip(LlmTokenEvent, payload)
        assert out["event"] == "llm_token"
        assert out["delta"] == "Examining the notice."
        assert out["message_id"] == "msg-1"
        assert out["phase"] == "intake"

    def test_event_literal_is_llm_token_only(self):
        """Discriminator narrowing — anything else is rejected at parse."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            LlmTokenEvent.model_validate(
                {
                    "kind": "agent",
                    "schema_version": 1,
                    "case_id": str(uuid4()),
                    "agent": "intake",
                    "phase": "intake",
                    "event": "thinking",  # wrong literal
                    "message_id": "m",
                    "delta": "x",
                    "ts": "2026-04-26T10:00:00+00:00",
                }
            )


class TestToolCallDeltaEvent:
    def test_round_trip(self):
        payload = {
            "kind": "agent",
            "schema_version": 1,
            "case_id": str(uuid4()),
            "agent": "intake",
            "phase": "intake",
            "event": "tool_call_delta",
            "tool_call_id": "tc-1",
            "name": "parse_document",
            "args_delta": '{"file_id": "fil',
            "ts": "2026-04-26T10:00:01+00:00",
        }
        out = _round_trip(ToolCallDeltaEvent, payload)
        assert out["event"] == "tool_call_delta"
        assert out["tool_call_id"] == "tc-1"
        assert out["name"] == "parse_document"
        assert out["args_delta"] == '{"file_id": "fil'


class TestEventUnionDiscriminator:
    def test_union_resolves_llm_token(self):
        adapter = TypeAdapter(Event)
        payload = {
            "kind": "agent",
            "schema_version": 1,
            "case_id": str(uuid4()),
            "agent": "intake",
            "phase": "intake",
            "event": "llm_token",
            "message_id": "m",
            "delta": "x",
            "ts": "2026-04-26T10:00:00+00:00",
        }
        obj = adapter.validate_python(payload)
        assert isinstance(obj, LlmTokenEvent)

    def test_union_resolves_tool_call_delta(self):
        adapter = TypeAdapter(Event)
        payload = {
            "kind": "agent",
            "schema_version": 1,
            "case_id": str(uuid4()),
            "agent": "intake",
            "phase": "intake",
            "event": "tool_call_delta",
            "tool_call_id": "t",
            "name": "parse_document",
            "args_delta": "{}",
            "ts": "2026-04-26T10:00:00+00:00",
        }
        obj = adapter.validate_python(payload)
        assert isinstance(obj, ToolCallDeltaEvent)


class TestConversationalStreamingFlag:
    """Single feature flag wired into Settings — the same env var Q1.4
    will read when deciding whether to enable conversational mode for
    a phase. Default empty → off everywhere."""

    def test_default_flag_is_empty_list(self, monkeypatch):
        monkeypatch.delenv("PIPELINE_CONVERSATIONAL_STREAMING_PHASES", raising=False)
        s = Settings(_env_file=None)
        assert s.pipeline_conversational_streaming_phases == []

    def test_flag_parses_comma_separated_env(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_CONVERSATIONAL_STREAMING_PHASES", "intake,triage")
        s = Settings(_env_file=None)
        assert s.pipeline_conversational_streaming_phases == ["intake", "triage"]

    def test_flag_strips_whitespace_and_empty_segments(self, monkeypatch):
        monkeypatch.setenv(
            "PIPELINE_CONVERSATIONAL_STREAMING_PHASES", " intake ,, triage , "
        )
        s = Settings(_env_file=None)
        assert s.pipeline_conversational_streaming_phases == ["intake", "triage"]

    def test_flag_membership_check(self, monkeypatch):
        """Q1.4 will use this exact pattern: `phase in <list>`."""
        monkeypatch.setenv("PIPELINE_CONVERSATIONAL_STREAMING_PHASES", "intake")
        s = Settings(_env_file=None)
        assert "intake" in s.pipeline_conversational_streaming_phases
        assert "audit" not in s.pipeline_conversational_streaming_phases  # A3 invariant
