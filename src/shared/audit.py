from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from src.shared.case_state import AuditEntry, CaseState


def append_audit_entry(
    state: CaseState,
    *,
    agent: str,
    action: str,
    input_payload: dict[str, Any] | None = None,
    output_payload: dict[str, Any] | None = None,
    system_prompt: str | None = None,
    llm_response: dict[str, Any] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    model: str | None = None,
    token_usage: dict[str, Any] | None = None,
    # Sprint 4 4.C4.2 — observability + cost + provenance + redaction.
    # All optional; pre-Sprint-4 callers continue to work unchanged.
    trace_id: str | None = None,
    span_id: str | None = None,
    retrieved_source_ids: list[str] | None = None,
    cost_usd: Decimal | None = None,
    redaction_applied: bool = False,
) -> CaseState:
    """Append an audit entry to the CaseState. Returns a new CaseState.

    The new observability fields (trace_id, span_id, retrieved_source_ids,
    cost_usd, redaction_applied) flow into ``audit_logs`` rows when the
    pipeline persists at gate end (``persist_case_results._insert_audit_log``).
    """
    entry = AuditEntry(
        agent=agent,
        timestamp=datetime.now(UTC),
        action=action,
        input_payload=input_payload,
        output_payload=output_payload,
        system_prompt=system_prompt,
        llm_response=llm_response,
        tool_calls=tool_calls,
        model=model,
        token_usage=token_usage,
        trace_id=trace_id,
        span_id=span_id,
        retrieved_source_ids=retrieved_source_ids,
        cost_usd=cost_usd,
        redaction_applied=redaction_applied,
    )
    return state.model_copy(update={"audit_log": [*state.audit_log, entry]})
