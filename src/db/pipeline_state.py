"""Mid-pipeline persistence for the mesh runner.

The sequential in-process runner never persisted mid-pipeline: the
whole chain lives or dies together in one Python process. The mesh
runner runs agents across a Solace boundary, so a crash after agent N
loses everything unless we checkpoint per-step.

`persist_case_state` is the minimum viable checkpoint — one row per
(case_id, run_id) that carries the latest full CaseState. The mesh
runner calls it after each agent resolves and after the L2 barrier
fires. On crash, callers can resume from the last persisted row.

`load_case_state` is the complementary reader. The What-If pipeline
uses it to rehydrate the real terminal CaseState for a completed
case — previously what-if constructed an empty CaseState and diffed
it against a re-run, which is garbage.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.case_state import CaseState

logger = logging.getLogger(__name__)


# Writer stamp — the version `persist_case_state` writes onto every new
# checkpoint row. Bumped when the CaseState shape changes in a way that
# requires a new version number.
CURRENT_SCHEMA_VERSION = 2

# Reader-accept set — checkpoints with `schema_version` outside this set
# are rejected. Diverges from `CURRENT_SCHEMA_VERSION` during a bake
# window so a reader-side compatibility shim can ship and roll one full
# release cycle BEFORE the writer flips. Today's window:
#   - Q2.3a (this commit) — reader accepts {2, 3}; CaseState default is 2,
#     so newly-constructed states still serialize as v2.
#   - Q2.3b (later) — CaseState default flips to 3; reader continues to
#     accept both so checkpoints written during the rollout co-exist.
# The writer (`persist_case_state`) does NOT override `schema_version` —
# it serializes whatever the in-memory CaseState carries. So a v3 row
# loaded today round-trips back as v3 and `intake_extraction` is
# preserved (locked in `test_v3_checkpoint_round_trips_through_existing_writer`).
SUPPORTED_READ_SCHEMA_VERSIONS = frozenset({2, 3})


class CheckpointSchemaMismatchError(RuntimeError):
    """Checkpoint row's schema_version does not match CURRENT_SCHEMA_VERSION."""


class CheckpointCorruptError(RuntimeError):
    """Checkpoint row is missing schema_version or fails CaseState validation."""


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


_SELECT_SQL = text(
    """
    SELECT case_state
    FROM pipeline_checkpoints
    WHERE case_id = :case_id AND run_id = :run_id
    """
)


# Transient failures (connection reset, deadlock, timeout) deserve a
# bounded retry because the checkpoint write is idempotent upsert.
# Permanent failures (IntegrityError — schema drift, constraint
# violation) must raise: silently dropping them hides the bug while
# the pipeline continues with stale state. Truly unknown exceptions
# still log+swallow to preserve the original non-fatal contract for
# the mesh runner's per-step checkpoints.
_CHECKPOINT_MAX_RETRIES = 3
_CHECKPOINT_RETRY_BASE_DELAY_SECONDS = 0.2


async def persist_case_state(
    db: AsyncSession,
    *,
    case_id: UUID | str,
    run_id: str,
    agent_name: str,
    state: CaseState,
) -> None:
    """Upsert a checkpoint row for `(case_id, run_id)` carrying the latest CaseState.

    Retries transient connectivity/deadlock errors up to 3x with
    exponential backoff. Raises `IntegrityError` on constraint
    violations (caller's outer `except Exception` in the mesh runner
    will route these through `_emit_terminal(reason="exception")`).
    Unknown exceptions log + swallow so a single freak DB hiccup
    cannot tear down an otherwise-healthy run.
    """
    payload = _serialize(state)
    params = {
        "case_id": str(case_id),
        "run_id": run_id,
        "agent_name": agent_name,
        "state": payload,
    }

    for attempt in range(1, _CHECKPOINT_MAX_RETRIES + 1):
        try:
            await db.execute(_UPSERT_SQL, params)
            await db.commit()
            return
        except IntegrityError:
            # Schema drift or FK violation — fail loud so the outer
            # runner treats this run as terminal/exception.
            with contextlib.suppress(Exception):
                await db.rollback()
            raise
        except (OperationalError, DBAPIError) as exc:
            with contextlib.suppress(Exception):
                await db.rollback()
            if attempt < _CHECKPOINT_MAX_RETRIES:
                delay = _CHECKPOINT_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "pipeline_checkpoint upsert transient failure (case_id=%s run_id=%s agent=%s attempt=%d/%d): %s",  # noqa: E501
                    case_id,
                    run_id,
                    agent_name,
                    attempt,
                    _CHECKPOINT_MAX_RETRIES,
                    exc,
                )
                await asyncio.sleep(delay)
                continue
            # Exhausted retries: log at error and swallow (non-fatal
            # contract — run continues without this checkpoint).
            logger.error(
                "pipeline_checkpoint upsert exhausted retries (case_id=%s run_id=%s agent=%s): %s",
                case_id,
                run_id,
                agent_name,
                exc,
            )
            return
        except Exception as exc:
            logger.error(
                "pipeline_checkpoint upsert unknown failure (case_id=%s run_id=%s agent=%s): %s",
                case_id,
                run_id,
                agent_name,
                exc,
            )
            with contextlib.suppress(Exception):
                await db.rollback()
            return


async def load_case_state(
    db: AsyncSession,
    *,
    case_id: UUID | str,
    run_id: str,
) -> CaseState | None:
    """Load a persisted CaseState for `(case_id, run_id)`.

    Returns None if no row exists. Raises CheckpointSchemaMismatchError
    if the row's schema_version does not match CURRENT_SCHEMA_VERSION,
    and CheckpointCorruptError if the row's JSON is missing
    schema_version or fails CaseState validation. Both errors are
    fail-loud: callers should surface them rather than construct a
    degraded state.
    """
    result = await db.execute(
        _SELECT_SQL,
        {"case_id": str(case_id), "run_id": run_id},
    )
    row = result.first()
    if row is None:
        return None

    raw = row[0]
    # Postgres JSONB decodes to dict/list; some drivers hand back a JSON
    # string. Normalize before inspecting schema_version so we can fail
    # on the raw shape rather than after pydantic silently defaults.
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CheckpointCorruptError(
                f"checkpoint for case_id={case_id} run_id={run_id} is not valid JSON"
            ) from exc

    if not isinstance(raw, dict) or "schema_version" not in raw:
        raise CheckpointCorruptError(
            f"checkpoint for case_id={case_id} run_id={run_id} is missing schema_version"
        )

    version = raw["schema_version"]
    if version not in SUPPORTED_READ_SCHEMA_VERSIONS:
        raise CheckpointSchemaMismatchError(
            f"checkpoint for case_id={case_id} run_id={run_id} has "
            f"schema_version={version!r}, expected one of "
            f"{sorted(SUPPORTED_READ_SCHEMA_VERSIONS)}"
        )

    try:
        return CaseState.model_validate(raw)
    except ValidationError as exc:
        raise CheckpointCorruptError(
            f"checkpoint for case_id={case_id} run_id={run_id} failed CaseState validation"
        ) from exc


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
