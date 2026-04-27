"""Planner unit tests for `scripts/migrate_in_flight_cases.py` (Sprint 2 2.A2.7).

The migrator reads the legacy `pipeline_checkpoints` table and seeds
matching threads in the AsyncPostgresSaver so cases that were mid-flight
at cutover keep resuming. Idempotency hinges on a pure planning step:

  * dedupe to one row per case (latest checkpoint wins)
  * skip cases the saver already knows about
  * count migrate-vs-skip totals for the dry-run report

Those pieces are testable without DB access — exercise them here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from scripts.migrate_in_flight_cases import (
    CheckpointRow,
    MigrationPlan,
    plan_migrations,
    render_dry_run_report,
)
from src.shared.case_state import CaseState

CASE_A = UUID("11111111-1111-1111-1111-111111111111")
CASE_B = UUID("22222222-2222-2222-2222-222222222222")
CASE_C = UUID("33333333-3333-3333-3333-333333333333")
T0 = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)


def _row(case_id: UUID, run_id: str, *, t: datetime, agent: str = "intake") -> CheckpointRow:
    return CheckpointRow(
        case_id=case_id,
        run_id=run_id,
        agent_name=agent,
        updated_at=t,
        case_state=CaseState(case_id=str(case_id), run_id=run_id),
    )


def test_plan_picks_latest_checkpoint_per_case() -> None:
    rows = [
        _row(CASE_A, "run-a-old", t=T0, agent="intake"),
        _row(CASE_A, "run-a-new", t=T0 + timedelta(minutes=5), agent="research-evidence"),
        _row(CASE_B, "run-b-only", t=T0 + timedelta(minutes=2)),
    ]

    plans = plan_migrations(rows, already_migrated=lambda _tid: False)

    by_case = {p.thread_id: p for p in plans}
    assert set(by_case) == {str(CASE_A), str(CASE_B)}
    assert by_case[str(CASE_A)].source_run_id == "run-a-new"
    assert by_case[str(CASE_A)].source_agent == "research-evidence"


def test_plan_skips_cases_already_migrated() -> None:
    rows = [
        _row(CASE_A, "run-a", t=T0),
        _row(CASE_B, "run-b", t=T0),
        _row(CASE_C, "run-c", t=T0),
    ]
    seen = {str(CASE_A), str(CASE_C)}

    plans = plan_migrations(rows, already_migrated=lambda tid: tid in seen)

    assert {p.thread_id for p in plans} == {str(CASE_B)}


def test_plan_is_idempotent_on_repeated_input() -> None:
    rows = [
        _row(CASE_A, "run-a", t=T0),
        _row(CASE_B, "run-b", t=T0),
    ]
    not_migrated = lambda _tid: False  # noqa: E731

    first = plan_migrations(rows, already_migrated=not_migrated)
    second = plan_migrations(rows, already_migrated=not_migrated)

    assert [p.thread_id for p in first] == [p.thread_id for p in second]
    assert [p.source_run_id for p in first] == [p.source_run_id for p in second]


def test_dry_run_report_includes_counts() -> None:
    rows = [
        _row(CASE_A, "run-a", t=T0),
        _row(CASE_B, "run-b", t=T0),
        _row(CASE_C, "run-c", t=T0),
    ]
    plans = plan_migrations(rows, already_migrated=lambda tid: tid == str(CASE_C))
    report = render_dry_run_report(plans, total_rows=len(rows), skipped=1)

    assert f"to migrate: {len(plans)}" in report.lower()
    assert "skipped: 1" in report.lower()
    assert str(CASE_A) in report
    assert str(CASE_B) in report
    assert str(CASE_C) not in report  # already-migrated cases are not relisted


def test_migration_plan_round_trips_case_state() -> None:
    state = CaseState(case_id=str(CASE_A), run_id="run-a", case_metadata={"hello": "world"})
    plan = MigrationPlan(
        thread_id=str(CASE_A),
        source_run_id="run-a",
        source_agent="intake",
        source_updated_at=T0,
        case_state=state,
    )
    # Plans carry the full CaseState so the migrator can write it into
    # the saver without an additional fetch.
    assert plan.case_state.case_metadata == {"hello": "world"}
