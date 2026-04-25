"""Audit middleware: records every tool invocation to the audit log.

Sprint 1 1.A1.2 ships the wire surface with a thin DB-direct writer.
Sprint 4 4.C4.2 swaps in the full audit upgrade (trace_id, span_id,
retrieved_source_ids, cost_usd, redaction_applied,
judge_correction_id) once migration 0025 lands.

The middleware reads `case_id` / `agent_name` from agent state (see
`state.CaseAwareState`) and writes one row per tool call via the local
`append_audit_entry` async helper. Tests stub that helper; production
inserts into `audit_logs`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from langchain.agents.middleware import wrap_tool_call

from src.models.audit import AuditLog
from src.services.database import async_session

logger = logging.getLogger(__name__)


def _state_field(state: Any, name: str) -> str:
    if isinstance(state, dict):
        return str(state.get(name, ""))
    return str(getattr(state, name, ""))


async def append_audit_entry(
    *,
    case_id: str,
    agent_name: str,
    action: str,
    input_payload: dict[str, Any] | None = None,
    output_payload: dict[str, Any] | None = None,
) -> None:
    """Insert one audit_logs row. Fire-and-forget — never raises into
    the caller, so a failed audit write doesn't break a running pipeline.
    """
    try:
        async with async_session() as db:
            db.add(
                AuditLog(
                    case_id=case_id,
                    agent_name=agent_name,
                    action=action,
                    input_payload=input_payload,
                    output_payload=output_payload,
                    created_at=datetime.now(UTC),
                )
            )
            await db.commit()
    except Exception:
        logger.exception("audit write failed for case_id=%s agent=%s", case_id, agent_name)


def _extract_source_ids(result: Any) -> list[str]:
    """Pull citation source_ids from a ToolMessage artifact, if present.

    Sprint 3 3.B.1/3.B.2 made the search tools emit `list[Document]`
    artifacts with stable `source_id` metadata. We stash them in the
    audit row's JSONB output_payload until 4.C4.1 promotes them to a
    dedicated `retrieved_source_ids` column.
    """
    artifact = getattr(result, "artifact", None)
    if not artifact:
        return []
    source_ids: list[str] = []
    for doc in artifact:
        meta = getattr(doc, "metadata", None) or {}
        source_id = meta.get("source_id")
        if source_id:
            source_ids.append(str(source_id))
    return source_ids


@wrap_tool_call
async def audit_tool_call(request, handler):  # noqa: ANN001
    """Record one audit row per tool invocation; never blocks tool execution."""
    case_id = _state_field(request.state, "case_id")
    agent_name = _state_field(request.state, "agent_name")
    tool_call = request.tool_call

    result = await handler(request)

    tool_result_text = str(getattr(result, "content", result))[:2000]
    source_ids = _extract_source_ids(result)
    await append_audit_entry(
        case_id=case_id,
        agent_name=agent_name,
        action="tool_call",
        input_payload={
            "tool_name": tool_call.get("name", ""),
            "args": tool_call.get("args", {}),
        },
        output_payload={"tool_result": tool_result_text, "source_ids": source_ids},
    )
    return result
