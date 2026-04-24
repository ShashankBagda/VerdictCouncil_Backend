"""Integration tests for Alembic migration 0019 — domains + cases.domain_id.

Skipped in CI unless ``INTEGRATION_TESTS=1`` is set. Requires a running
Postgres with migrations 0001-0018 applied (run ``make infra-up && make migrate``
then roll back to 0018 before running this suite).

These tests verify:
- Upgrade creates domains, domain_documents, and cases.domain_id column.
- Seed rows for small_claims and traffic_violation are inserted.
- Backfill sets domain_id on existing cases where cases.domain matches.
- Downgrade removes all added artifacts cleanly.
"""

from __future__ import annotations

import os
import uuid

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION_TESTS") != "1",
    reason="Integration tests require infrastructure (set INTEGRATION_TESTS=1)",
)


def _alembic_cfg() -> Config:
    cfg = Config("alembic.ini")
    return cfg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_tables(conn) -> list[str]:
    inspector = inspect(conn)
    return inspector.get_table_names()


def _get_columns(conn, table: str) -> list[str]:
    inspector = inspect(conn)
    return [c["name"] for c in inspector.get_columns(table)]


# ---------------------------------------------------------------------------
# Upgrade assertions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_0019_upgrade_creates_domains_table():
    """Upgrade must create the `domains` table."""
    from src.services.database import async_session

    cfg = _alembic_cfg()
    command.upgrade(cfg, "0019")

    async with async_session() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM domains"))
        row = result.scalar()
    assert row is not None


@pytest.mark.asyncio
async def test_migration_0019_upgrade_seeds_small_claims_and_traffic():
    """Upgrade must seed both built-in domain rows."""
    from src.services.database import async_session

    cfg = _alembic_cfg()
    command.upgrade(cfg, "0019")

    async with async_session() as session:
        result = await session.execute(
            text(
                "SELECT code FROM domains WHERE code IN ('small_claims', 'traffic_violation') ORDER BY code"  # noqa: E501
            )
        )
        codes = [r[0] for r in result.fetchall()]

    assert "small_claims" in codes
    assert "traffic_violation" in codes


@pytest.mark.asyncio
async def test_migration_0019_seeded_domains_are_inactive():
    """Seeded domains must have is_active=false until the provisioning script runs."""
    from src.services.database import async_session

    cfg = _alembic_cfg()
    command.upgrade(cfg, "0019")

    async with async_session() as session:
        result = await session.execute(
            text(
                "SELECT code, is_active FROM domains WHERE code IN ('small_claims', 'traffic_violation')"  # noqa: E501
            )
        )
        rows = {r[0]: r[1] for r in result.fetchall()}

    assert rows.get("small_claims") is False, "small_claims seed must be inactive"
    assert rows.get("traffic_violation") is False, "traffic_violation seed must be inactive"


@pytest.mark.asyncio
async def test_migration_0019_upgrade_adds_domain_id_to_cases():
    """Upgrade must add the nullable domain_id column to the cases table."""
    from src.services.database import async_session

    cfg = _alembic_cfg()
    command.upgrade(cfg, "0019")

    async with async_session() as session:
        result = await session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='cases' AND column_name='domain_id'"
            )
        )
        row = result.fetchone()

    assert row is not None, "cases.domain_id column must exist after 0019 upgrade"


@pytest.mark.asyncio
async def test_migration_0019_backfill_sets_domain_id():
    """Existing cases with domain='small_claims' must have domain_id set after backfill."""
    from src.models.case import Case, CaseDomain, CaseStatus
    from src.models.user import User, UserRole
    from src.services.database import async_session

    cfg = _alembic_cfg()
    # Apply migration up to 0018 to insert a test row, then upgrade to 0019
    command.upgrade(cfg, "0018")

    async with async_session() as session:
        user = User(
            id=uuid.uuid4(),
            email=f"test_{uuid.uuid4().hex[:8]}@example.com",
            password_hash="hashed",
            role=UserRole.judge,
            name="Test Judge",
        )
        session.add(user)
        await session.flush()

        case = Case(
            id=uuid.uuid4(),
            domain=CaseDomain.small_claims,
            status=CaseStatus.processing,
            created_by=user.id,
            title="Backfill Test Case",
        )
        session.add(case)
        await session.commit()
        case_id = case.id

    command.upgrade(cfg, "0019")

    async with async_session() as session:
        result = await session.execute(
            text("SELECT domain_id FROM cases WHERE id = :cid").bindparams(cid=str(case_id))
        )
        domain_id_val = result.scalar()

    assert domain_id_val is not None, "Backfill must set domain_id for existing small_claims case"


# ---------------------------------------------------------------------------
# Downgrade assertions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_0019_downgrade_removes_domains_table():
    """Downgrade must drop the domains and domain_documents tables."""
    from src.services.database import async_session

    cfg = _alembic_cfg()
    command.upgrade(cfg, "0019")
    command.downgrade(cfg, "0018")

    async with async_session() as session:
        result = await session.execute(
            text(
                "SELECT table_name FROM information_schema.tables WHERE table_name IN ('domains', 'domain_documents')"  # noqa: E501
            )
        )
        remaining = [r[0] for r in result.fetchall()]

    assert "domains" not in remaining, "domains table must be dropped on downgrade"
    assert "domain_documents" not in remaining, (
        "domain_documents table must be dropped on downgrade"
    )  # noqa: E501


@pytest.mark.asyncio
async def test_migration_0019_downgrade_removes_domain_id_column():
    """Downgrade must remove cases.domain_id column."""
    from src.services.database import async_session

    cfg = _alembic_cfg()
    command.upgrade(cfg, "0019")
    command.downgrade(cfg, "0018")

    async with async_session() as session:
        result = await session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='cases' AND column_name='domain_id'"
            )
        )
        row = result.fetchone()

    assert row is None, "cases.domain_id must be dropped on migration downgrade"
