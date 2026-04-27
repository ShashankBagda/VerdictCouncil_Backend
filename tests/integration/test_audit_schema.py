"""Integration tests for Alembic migration 0026 — audit schema upgrade.

Covers Sprint 4 4.C4.1:
- New tables `judge_corrections` + `suppressed_citations` exist post-upgrade
- `audit_logs` gains `trace_id` / `span_id` / `retrieved_source_ids` /
  `cost_usd` / `redaction_applied` / `judge_correction_id`
- FK on `case_id` rejects non-existent UUIDs and cascades on case delete
- CHECK constraints reject invalid `phase` and `subagent` combos
- Round-trip: upgrade → downgrade → upgrade leaves a clean schema

Skipped in CI unless INTEGRATION_TESTS=1 is set; requires a running
Postgres with migrations 0001-0025 applied.
"""

from __future__ import annotations

import os
import uuid

import pytest
from alembic.config import Config
from sqlalchemy import text

from alembic import command

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION_TESTS") != "1",
    reason="Integration tests require infrastructure (set INTEGRATION_TESTS=1)",
)


def _cfg() -> Config:
    return Config("alembic.ini")


# ---------------------------------------------------------------------------
# Schema shape after upgrade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upgrade_creates_judge_corrections_table():
    from src.services.database import async_session

    command.upgrade(_cfg(), "0026")
    async with async_session() as session:
        rows = await session.execute(text("SELECT COUNT(*) FROM judge_corrections"))
        assert rows.scalar() is not None


@pytest.mark.asyncio
async def test_upgrade_creates_suppressed_citations_table():
    from src.services.database import async_session

    command.upgrade(_cfg(), "0026")
    async with async_session() as session:
        rows = await session.execute(text("SELECT COUNT(*) FROM suppressed_citations"))
        assert rows.scalar() is not None


@pytest.mark.asyncio
async def test_upgrade_adds_audit_logs_columns():
    from src.services.database import async_session

    command.upgrade(_cfg(), "0026")
    async with async_session() as session:
        result = await session.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name='audit_logs'")
        )
        cols = {r[0] for r in result.fetchall()}
    for col in {
        "trace_id",
        "span_id",
        "retrieved_source_ids",
        "cost_usd",
        "redaction_applied",
        "judge_correction_id",
    }:
        assert col in cols, f"audit_logs.{col} must exist after 0026"


# ---------------------------------------------------------------------------
# FK + CHECK enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_corrections_rejects_nonexistent_case_id():
    from sqlalchemy.exc import IntegrityError

    from src.services.database import async_session

    command.upgrade(_cfg(), "0026")
    bogus = uuid.uuid4()

    async with async_session() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO judge_corrections "
                    "(case_id, run_id, phase, correction_text) "
                    "VALUES (:case_id, 'run-1', 'intake', 'oops')"
                ),
                {"case_id": str(bogus)},
            )
            await session.commit()


@pytest.mark.asyncio
async def test_suppressed_citations_rejects_invalid_phase():
    from sqlalchemy.exc import IntegrityError

    from src.services.database import async_session

    command.upgrade(_cfg(), "0026")

    async with async_session() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO suppressed_citations "
                    "(case_id, run_id, phase, citation_text, reason) "
                    "VALUES (:case_id, 'r', 'BAD_PHASE', 'cite', 'no_source_match')"
                ),
                {"case_id": str(uuid.uuid4())},
            )
            await session.commit()


@pytest.mark.asyncio
async def test_subagent_only_allowed_in_research_phase():
    from sqlalchemy.exc import IntegrityError

    from src.services.database import async_session

    command.upgrade(_cfg(), "0026")
    async with async_session() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO suppressed_citations "
                    "(case_id, run_id, phase, subagent, citation_text, reason) "
                    "VALUES (:cid, 'r', 'intake', 'evidence', 'cite', 'no_source_match')"
                ),
                {"cid": str(uuid.uuid4())},
            )
            await session.commit()


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_downgrade_drops_new_tables_and_columns():
    from src.services.database import async_session

    command.upgrade(_cfg(), "0026")
    command.downgrade(_cfg(), "0025")

    async with async_session() as session:
        tables = await session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name IN ('judge_corrections','suppressed_citations')"
            )
        )
        assert tables.fetchall() == []

        cols = await session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='audit_logs' "
                "AND column_name IN ('trace_id','span_id','retrieved_source_ids',"
                "'cost_usd','redaction_applied','judge_correction_id')"
            )
        )
        assert cols.fetchall() == []

    # Bring schema back to head so other tests aren't disrupted.
    command.upgrade(_cfg(), "head")


@pytest.mark.asyncio
async def test_upgrade_then_downgrade_then_upgrade_is_clean():
    command.upgrade(_cfg(), "0026")
    command.downgrade(_cfg(), "0025")
    command.upgrade(_cfg(), "0026")

    from src.services.database import async_session

    async with async_session() as session:
        inspector = await session.connection()
        # Sanity: tables exist after the round-trip
        result = await session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name IN ('judge_corrections','suppressed_citations') "
                "ORDER BY table_name"
            )
        )
        names = [r[0] for r in result.fetchall()]
        assert names == ["judge_corrections", "suppressed_citations"]
        del inspector  # quiet ruff


# ---------------------------------------------------------------------------
# ON DELETE CASCADE (case parent removal)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_case_deletion_cascades_to_judge_corrections():
    from src.models.case import Case, CaseDomain, CaseStatus
    from src.models.user import User, UserRole
    from src.services.database import async_session

    command.upgrade(_cfg(), "0026")

    async with async_session() as session:
        user = User(
            id=uuid.uuid4(),
            email=f"cascade_{uuid.uuid4().hex[:8]}@example.com",
            password_hash="hashed",
            role=UserRole.judge,
            name="Cascade Judge",
        )
        case = Case(
            id=uuid.uuid4(),
            domain=CaseDomain.small_claims,
            status=CaseStatus.draft,
            created_by=user.id,
        )
        session.add(user)
        session.add(case)
        await session.flush()

        await session.execute(
            text(
                "INSERT INTO judge_corrections "
                "(case_id, run_id, phase, correction_text) "
                "VALUES (:cid, 'r', 'intake', 'note')"
            ),
            {"cid": str(case.id)},
        )
        await session.commit()

        await session.delete(case)
        await session.commit()

        rows = await session.execute(
            text("SELECT COUNT(*) FROM judge_corrections WHERE case_id = :cid"),
            {"cid": str(case.id)},
        )
        assert rows.scalar() == 0
