"""Stuck-case watchdog.

Scans the `cases` table for rows that have been in `processing` for longer
than ``threshold_seconds`` and marks them as ``failed_retryable``.

Why this exists
---------------
``CaseState`` lives inside an in-flight LangGraph run while a pipeline is
executing. If the arq worker process crashes mid-run (OOM kill, K8s
SIGTERM during a rollout, exception escaping the retry policy), the
matching ``Case`` row stays in ``processing`` forever — the user sees a
spinner that never resolves. This watchdog gives the user a clear "retry"
affordance instead of a silent hang.

Conservative on purpose: marks as ``failed_retryable`` rather than
auto-resuming. Auto-resume would require verifying agent idempotency on
``thread_id`` reuse against the Postgres checkpointer, which is out of
scope for this watchdog.

CLI
---
    python -m src.services.stuck_case_watchdog [--threshold-seconds N] [--dry-run]

Runs in production as the K8s CronJob in
``k8s/base/cronjob-stuck-case-watchdog.yaml`` (5-minute schedule,
30-minute threshold).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.case import Case, CaseStatus
from src.services.database import async_session

logger = logging.getLogger(__name__)

# 30 min default. Pipeline normal runtime is single-digit minutes; 30 min is a
# generous floor that should not produce false positives.
DEFAULT_THRESHOLD_SECONDS = 1800


async def find_and_mark_stuck_cases(
    session: AsyncSession,
    threshold_seconds: int = DEFAULT_THRESHOLD_SECONDS,
    dry_run: bool = False,
) -> list[str]:
    """Find cases stuck in `processing` and mark them `failed_retryable`.

    The "last activity" timestamp uses ``COALESCE(updated_at, created_at)``
    because :class:`TimestampMixin` leaves ``updated_at`` ``NULL`` until the
    row is first updated — see ``src/models/base.py:17-19``. A brand-new
    `processing` case has ``updated_at IS NULL`` and would otherwise never
    match.

    Returns the list of case IDs (as strings) that were marked. When
    ``dry_run`` is ``True``, the IDs are returned but no rows are updated.
    """
    last_activity = func.coalesce(Case.updated_at, Case.created_at)
    cutoff = func.now() - func.make_interval(0, 0, 0, 0, 0, 0, threshold_seconds)

    rows = await session.execute(
        select(Case.id).where(Case.status == CaseStatus.processing, last_activity < cutoff)
    )
    stuck_ids = [str(row[0]) for row in rows.all()]

    if not stuck_ids:
        return []

    if dry_run:
        for case_id in stuck_ids:
            logger.info("dry-run: would mark case %s as failed_retryable", case_id)
        return stuck_ids

    await session.execute(
        update(Case)
        .where(Case.id.in_([row for row in stuck_ids]))
        .values(status=CaseStatus.failed_retryable)
    )
    await session.commit()

    for case_id in stuck_ids:
        # Structured log so this lands cleanly in any aggregator. One line per
        # case so we can count occurrences without parsing summary lines.
        logger.warning(
            "stuck_case_marked_failed_retryable",
            extra={"case_id": case_id, "threshold_seconds": threshold_seconds},
        )
    return stuck_ids


async def _run(threshold_seconds: int, dry_run: bool) -> int:
    async with async_session() as session:
        marked = await find_and_mark_stuck_cases(
            session, threshold_seconds=threshold_seconds, dry_run=dry_run
        )
    return len(marked)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--threshold-seconds",
        type=int,
        default=DEFAULT_THRESHOLD_SECONDS,
        help="A case is stuck if its last activity is older than this. Default: 1800 (30 min).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log which cases would be marked but do not write.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    count = asyncio.run(_run(args.threshold_seconds, args.dry_run))
    action = "would-mark" if args.dry_run else "marked"
    logger.info("watchdog complete: %s %d case(s)", action, count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
