"""Mid-pipeline persistence for the mesh runner.

The sequential in-process runner never persisted mid-pipeline: the
whole chain lives or dies together in one Python process. The mesh
runner runs agents across a Solace boundary, so a crash after agent N
loses everything unless we checkpoint per-step.

`persist_case_state` is the minimum viable checkpoint — one row per
(case_id, run_id) that carries the latest full CaseState. The mesh
runner calls it after each agent resolves and after the L2 barrier
fires. On crash, callers can resume from the last persisted row.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.case_state import CaseState

logger = logging.getLogger(__name__)


_UPSERT_SQL = text(
    """
    INSERT INTO pipeline_checkpoints (case_id, run_id, agent_name, case_state, updated_at)
    VALUES (:case_id, :run_id, :agent_name, CAST(:state AS JSONB), NOW())
    ON CONFLICT (case_id, run_id) DO UPDATE
    SET agent_name = EXCLUDED.agent_name,
        case_state = EXCLUDED.case_state,
        updated_at = NOW()
    """
)


async def persist_case_state(
    db: AsyncSession,
    *,
    case_id: UUID | str,
    run_id: str,
    agent_name: str,
    state: CaseState,
) -> None:
    """Upsert a checkpoint row for `(case_id, run_id)` carrying the latest CaseState.

    Non-fatal: logs and swallows SQLAlchemy errors so a DB hiccup
    doesn't tear down an otherwise-healthy pipeline run. Callers that
    care about durability should monitor the `pipeline_checkpoints`
    table separately.
    """
    try:
        payload = _serialize(state)
        await db.execute(
            _UPSERT_SQL,
            {
                "case_id": str(case_id),
                "run_id": run_id,
                "agent_name": agent_name,
                "state": payload,
            },
        )
        await db.commit()
    except Exception as exc:
        logger.error(
            "pipeline_checkpoint upsert failed (case_id=%s run_id=%s agent=%s): %s",
            case_id,
            run_id,
            agent_name,
            exc,
        )
        try:
            await db.rollback()
        except Exception:
            pass


def _serialize(state: CaseState) -> str:
    """Serialize a CaseState to JSON, handling UUID + datetime via pydantic."""
    if hasattr(state, "model_dump_json"):
        return state.model_dump_json()
    # Fallback for non-pydantic inputs (tests may pass plain dicts)
    return json.dumps(state, default=_json_default)


def _json_default(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Unserializable: {type(value).__name__}")
