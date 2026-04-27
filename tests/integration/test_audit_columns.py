"""Sprint 4 4.C4.2 — append_audit_entry persists new observability columns."""

from __future__ import annotations

import uuid
from decimal import Decimal

from src.shared.audit import append_audit_entry
from src.shared.case_state import CaseState


def _state() -> CaseState:
    return CaseState(case_id=str(uuid.uuid4()))


def test_append_audit_entry_default_values() -> None:
    state = append_audit_entry(_state(), agent="evidence", action="tool_call")
    entry = state.audit_log[-1]
    # Pre-Sprint-4 callers continue to work — defaults are clean.
    assert entry.trace_id is None
    assert entry.span_id is None
    assert entry.retrieved_source_ids is None
    assert entry.cost_usd is None
    assert entry.redaction_applied is False


def test_append_audit_entry_persists_observability_fields() -> None:
    state = append_audit_entry(
        _state(),
        agent="research_law",
        action="llm_response",
        trace_id="0123456789abcdef0123456789abcdef",
        span_id="0123456789abcdef",
        retrieved_source_ids=["src-1", "src-2"],
        cost_usd=Decimal("0.012345"),
        redaction_applied=True,
    )
    entry = state.audit_log[-1]
    assert entry.trace_id == "0123456789abcdef0123456789abcdef"
    assert entry.span_id == "0123456789abcdef"
    assert entry.retrieved_source_ids == ["src-1", "src-2"]
    assert entry.cost_usd == Decimal("0.012345")
    assert entry.redaction_applied is True


def test_audit_entry_round_trips_through_pydantic() -> None:
    state = append_audit_entry(
        _state(),
        agent="auditor",
        action="check_complete",
        cost_usd=Decimal("0.001"),
        retrieved_source_ids=[],
    )
    entry = state.audit_log[-1]
    json_data = entry.model_dump(mode="json")
    assert json_data["cost_usd"] == "0.001"
    assert json_data["retrieved_source_ids"] == []
    # And back
    from src.shared.case_state import AuditEntry

    rehydrated = AuditEntry.model_validate(json_data)
    assert rehydrated.cost_usd == Decimal("0.001")
