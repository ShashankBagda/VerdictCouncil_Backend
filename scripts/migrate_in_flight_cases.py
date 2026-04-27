"""Sprint 2 2.A2.7 — seed AsyncPostgresSaver threads from `pipeline_checkpoints`.

The cutover deletes the legacy `pipeline_checkpoints` table once
AsyncPostgresSaver becomes the source of truth (2.A2.11). Cases that are
mid-flight at cutover would lose their state unless we copy their latest
checkpoint into a saver thread first.

Pipeline:
  1. SELECT one row per `(case_id)` (latest `updated_at`) from
     `pipeline_checkpoints` for cases not in a terminal status.
  2. Skip threads the saver already knows about — re-running the
     migrator must be a no-op (idempotency).
  3. Write the CaseState into the saver under `thread_id = case_id`
     using `graph.aupdate_state`, which produces a checkpoint that
     `aget_state(config)` can read back.

Usage:
    python scripts/migrate_in_flight_cases.py [--dry-run]

Pure functions (`plan_migrations`, `render_dry_run_report`) are unit
tested in `tests/unit/test_migrate_in_flight_cases.py`. The DB and saver
sides are integration boundaries; running with `--dry-run` exercises the
read path without touching the saver.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text

from src.shared.case_state import CaseState

# Cases in these statuses are terminal — no point migrating their state.
# Mirror src.models.case.CaseStatus enum literals.
_TERMINAL_STATUSES = ("closed", "failed")


@dataclass(frozen=True)
class CheckpointRow:
    """One row from `pipeline_checkpoints` decoded into typed fields."""

    case_id: UUID
    run_id: str
    agent_name: str
    updated_at: datetime
    case_state: CaseState


@dataclass(frozen=True)
class MigrationPlan:
    """The intent to write `case_state` into the saver under `thread_id`."""

    thread_id: str
    source_run_id: str
    source_agent: str
    source_updated_at: datetime
    case_state: CaseState


def plan_migrations(
    rows: Iterable[CheckpointRow],
    *,
    already_migrated: Callable[[str], bool],
) -> list[MigrationPlan]:
    """Pick the latest row per case_id, drop ones already in the saver.

    Args:
        rows: All in-flight `pipeline_checkpoints` rows. May contain
            multiple rows per case (one per gate / per agent).
        already_migrated: Predicate that returns True when the saver
            already has a thread for the given thread_id. Lets callers
            re-run the migrator after a partial pass without re-writing
            threads that landed cleanly the first time.
    """
    latest: dict[str, CheckpointRow] = {}
    for row in rows:
        thread_id = str(row.case_id)
        existing = latest.get(thread_id)
        if existing is None or row.updated_at > existing.updated_at:
            latest[thread_id] = row

    plans: list[MigrationPlan] = []
    for thread_id, row in latest.items():
        if already_migrated(thread_id):
            continue
        plans.append(
            MigrationPlan(
                thread_id=thread_id,
                source_run_id=row.run_id,
                source_agent=row.agent_name,
                source_updated_at=row.updated_at,
                case_state=row.case_state,
            )
        )
    plans.sort(key=lambda p: p.thread_id)
    return plans


def render_dry_run_report(
    plans: list[MigrationPlan],
    *,
    total_rows: int,
    skipped: int,
) -> str:
    """Human-readable summary printed during `--dry-run` so operators can sanity-check."""
    lines = [
        "Dry-run plan for migrate_in_flight_cases",
        f"  rows scanned: {total_rows}",
        f"  to migrate: {len(plans)}",
        f"  skipped: {skipped} (already in saver)",
        "",
        "Planned writes (thread_id ← latest run_id @ agent / updated_at):",
    ]
    for plan in plans:
        lines.append(
            f"  {plan.thread_id} ← {plan.source_run_id} @ "
            f"{plan.source_agent} / {plan.source_updated_at.isoformat()}"
        )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# DB / saver IO — exercised end-to-end via `python scripts/...` with --dry-run.
# Intentionally unverified by unit tests because they require Postgres + a live
# saver context; the planner above is the unit-testable seam.
# --------------------------------------------------------------------------- #


_SELECT_IN_FLIGHT_SQL = text(
    """
    SELECT pc.case_id, pc.run_id, pc.agent_name, pc.case_state, pc.updated_at
    FROM pipeline_checkpoints pc
    JOIN cases c ON c.id = pc.case_id
    WHERE c.status NOT IN :terminal
    ORDER BY pc.case_id, pc.updated_at DESC
    """
).bindparams(bindparam("terminal", expanding=True))


async def _read_in_flight_rows() -> list[CheckpointRow]:
    """SELECT in-flight checkpoints from Postgres into typed CheckpointRow values."""
    from src.services.database import async_session

    rows: list[CheckpointRow] = []
    async with async_session() as db:
        result = await db.execute(
            _SELECT_IN_FLIGHT_SQL,
            {"terminal": list(_TERMINAL_STATUSES)},
        )
        for r in result.all():
            raw = r.case_state
            case_state = CaseState.model_validate(raw)
            rows.append(
                CheckpointRow(
                    case_id=r.case_id,
                    run_id=r.run_id,
                    agent_name=r.agent_name,
                    updated_at=r.updated_at,
                    case_state=case_state,
                )
            )
    return rows


async def _saver_has_thread(graph: Any, thread_id: str) -> bool:
    """True when the saver already knows about `thread_id`.

    A saver returns a snapshot whose `.values` is empty when no checkpoint
    exists. We treat any non-empty snapshot as "already migrated" so reruns
    are no-ops.
    """
    snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    return bool(getattr(snapshot, "values", None))


async def _migrate_one(graph: Any, plan: MigrationPlan) -> None:
    """Write `plan.case_state` into the saver under its thread_id.

    Uses `aupdate_state` because the saver-API path is the same one
    `gate_run` reads from after cutover. `as_node="__start__"` marks the
    seed checkpoint as a graph-entry write so subsequent gates start
    cleanly from this state.
    """
    config = {"configurable": {"thread_id": plan.thread_id}}
    await graph.aupdate_state(config, {"case": plan.case_state}, as_node="__start__")


async def _amain(*, dry_run: bool) -> int:
    rows = await _read_in_flight_rows()

    if dry_run:
        plans = plan_migrations(rows, already_migrated=lambda _tid: False)
        # Without consulting the saver we can't compute "already migrated"
        # accurately in dry-run mode; report 0 and let the operator
        # interpret the count as an upper bound.
        print(render_dry_run_report(plans, total_rows=len(rows), skipped=0))
        return 0

    # Real run: open the saver lifespan, wire the graph, plan + apply.
    from src.pipeline.graph.builder import build_graph
    from src.pipeline.graph.checkpointer import lifespan_checkpointer
    from src.shared.config import settings

    if settings.langgraph_checkpointer == "disabled":
        print("ERROR: settings.langgraph_checkpointer is 'disabled'", file=sys.stderr)
        return 2

    async with lifespan_checkpointer(settings.database_url):
        graph = build_graph()

        async def _predicate(thread_id: str) -> bool:
            return await _saver_has_thread(graph, thread_id)

        # `plan_migrations` takes a sync predicate; collect the async results first.
        seen: dict[str, bool] = {}
        for row in rows:
            tid = str(row.case_id)
            if tid not in seen:
                seen[tid] = await _predicate(tid)

        plans = plan_migrations(rows, already_migrated=lambda tid: seen.get(tid, False))
        skipped = sum(1 for v in seen.values() if v)

        print(render_dry_run_report(plans, total_rows=len(rows), skipped=skipped))
        for plan in plans:
            await _migrate_one(graph, plan)
            print(f"  migrated: {plan.thread_id}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan + report without writing to the saver.",
    )
    args = parser.parse_args()
    return asyncio.run(_amain(dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
