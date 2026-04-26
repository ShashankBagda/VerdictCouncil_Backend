"""Alembic up/down round-trip tests for all migrations.

Verifies that every migration in the chain can be:
  1. Applied (upgrade)
  2. Reversed cleanly (downgrade -1)
  3. Re-applied without error (idempotency smoke)

These tests require a real Postgres instance and are skipped in the
standard CI unit-test job.  They run when ``INTEGRATION_TESTS=1`` is set —
targeted at the ``integration-tests`` CI job with a Postgres service.

Usage
-----
    INTEGRATION_TESTS=1 pytest tests/integration/test_alembic_rollback.py -v

The test expects a blank Postgres database accessible via the ``DATABASE_URL``
environment variable (or the ``alembic.ini`` default if unset).
"""

from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION_TESTS") != "1",
    reason="Requires live Postgres — set INTEGRATION_TESTS=1 to run",
)

# ---------------------------------------------------------------------------
# All migration revision IDs in chain order
# ---------------------------------------------------------------------------

REVISION_CHAIN: list[str] = [
    "0001",
    "0002",
    "0003",
    "0004",
    "0005",
    "0006",
    "0007",
    "0008",
    "0009",
    "0010",
    "0011",
    "0012",
    "0013",
    "0016",
    "0017",
    "0018",
    "0019",
    "0020",
    "0021",
    "0022",
    "0023",
    "0024",
    "0025",
    "0026",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _alembic_cfg() -> Config:
    cfg = Config("alembic.ini")
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _get_sync_engine():
    """Return a synchronous SQLAlchemy engine for inspection calls."""
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://verdictcouncil:verdictcouncil@localhost:5432/verdictcouncil_test",
    )
    return create_engine(db_url)


def _current_revision(engine) -> str | None:
    """Return the current alembic_version from the DB, or None if blank."""
    with engine.connect() as conn:
        try:
            result = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
            row = result.fetchone()
            return row[0] if row else None
        except Exception:
            return None


def _reset_database(engine) -> None:
    """Drop all objects (public schema) and recreate for a clean slate."""
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


# ---------------------------------------------------------------------------
# Full chain: upgrade head → downgrade base
# ---------------------------------------------------------------------------


class TestAlembicFullChain:
    """Apply all migrations to head, then roll back to base one at a time."""

    @pytest.fixture(autouse=True)
    def clean_database(self):
        """Start from a completely blank database for each test."""
        engine = _get_sync_engine()
        _reset_database(engine)
        yield
        engine.dispose()

    def test_upgrade_to_head_succeeds(self):
        """All migrations must apply to head without error."""
        cfg = _alembic_cfg()
        command.upgrade(cfg, "head")
        engine = _get_sync_engine()
        rev = _current_revision(engine)
        engine.dispose()
        assert rev == REVISION_CHAIN[-1], f"Expected head={REVISION_CHAIN[-1]}, got {rev!r}"

    def test_downgrade_base_succeeds(self):
        """Downgrade from head to base must complete without error."""
        cfg = _alembic_cfg()
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        engine = _get_sync_engine()
        rev = _current_revision(engine)
        engine.dispose()
        assert rev is None, f"Expected no revision after full downgrade, got {rev!r}"

    def test_full_round_trip_twice(self):
        """Upgrade → downgrade → upgrade again must succeed (idempotency)."""
        cfg = _alembic_cfg()
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
        engine = _get_sync_engine()
        rev = _current_revision(engine)
        engine.dispose()
        assert rev == REVISION_CHAIN[-1]


# ---------------------------------------------------------------------------
# Per-migration up/down round-trip
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _clean_once():
    """Blank the DB once before all per-migration tests in this module."""
    engine = _get_sync_engine()
    _reset_database(engine)
    engine.dispose()


class TestPerMigrationRollback:
    """For each migration in the chain, verify upgrade+downgrade round-trip."""

    @pytest.mark.parametrize("revision", REVISION_CHAIN)
    def test_upgrade_then_downgrade(self, revision: str):
        """Upgrade to ``revision`` then immediately downgrade must return to prior state."""
        cfg = _alembic_cfg()
        engine = _get_sync_engine()

        # Ensure we are at the revision just before this one
        if revision == REVISION_CHAIN[0]:
            # First migration — start from a blank DB
            _reset_database(engine)
            prior_rev = None
        else:
            idx = REVISION_CHAIN.index(revision)
            prior = REVISION_CHAIN[idx - 1]
            command.upgrade(cfg, prior)
            prior_rev = prior

        engine.dispose()

        # Apply this migration
        command.upgrade(cfg, revision)

        engine = _get_sync_engine()
        assert _current_revision(engine) == revision, (
            f"Expected {revision} after upgrade, got {_current_revision(engine)!r}"
        )
        engine.dispose()

        # Roll it back
        command.downgrade(cfg, "-1")

        engine = _get_sync_engine()
        actual = _current_revision(engine)
        engine.dispose()

        assert actual == prior_rev, (
            f"Expected {prior_rev!r} after downgrade from {revision}, got {actual!r}"
        )


# ---------------------------------------------------------------------------
# Schema integrity after round-trip
# ---------------------------------------------------------------------------


class TestSchemaIntegrityAfterRoundTrip:
    """Verify key tables still exist and have expected columns after upgrade."""

    @pytest.fixture(autouse=True)
    def _apply_head(self):
        engine = _get_sync_engine()
        _reset_database(engine)
        engine.dispose()
        cfg = _alembic_cfg()
        command.upgrade(cfg, "head")

    def _columns(self, table: str) -> set[str]:
        engine = _get_sync_engine()
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns(table)}
        engine.dispose()
        return cols

    def _tables(self) -> set[str]:
        engine = _get_sync_engine()
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        engine.dispose()
        return tables

    def test_users_table_exists_with_key_columns(self):
        tables = self._tables()
        assert "users" in tables
        cols = self._columns("users")
        for expected in ("id", "username", "hashed_password", "role"):
            assert expected in cols, f"Missing column {expected!r} in users"

    def test_cases_table_exists_with_key_columns(self):
        assert "cases" in self._tables()
        cols = self._columns("cases")
        for expected in ("id", "title", "status", "domain_id"):
            assert expected in cols, f"Missing column {expected!r} in cases"

    def test_domains_table_exists_with_key_columns(self):
        assert "domains" in self._tables()
        cols = self._columns("domains")
        for expected in ("id", "slug", "display_name"):
            assert expected in cols, f"Missing column {expected!r} in domains"

    def test_pipeline_jobs_table_exists(self):
        assert "pipeline_jobs" in self._tables()

    def test_pipeline_events_table_exists(self):
        assert "pipeline_events" in self._tables()

    def test_audit_logs_table_exists(self):
        tables = self._tables()
        # May be audit_logs or audit_entries depending on migration version
        assert any(t.startswith("audit") for t in tables), f"No audit table found in {tables}"

    def test_no_orphan_alembic_version(self):
        """alembic_version must contain exactly one row after head upgrade."""
        engine = _get_sync_engine()
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM alembic_version"))
            count = result.scalar()
        engine.dispose()
        assert count == 1
