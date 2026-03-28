from datetime import UTC, datetime
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
    solace_message_id: str | None = None,
) -> CaseState:
    """Append an audit entry to the CaseState. Returns a new CaseState."""
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
        solace_message_id=solace_message_id,
    )
    return state.model_copy(update={"audit_log": [*state.audit_log, entry]})
