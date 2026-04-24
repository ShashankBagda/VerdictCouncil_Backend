"""Unit tests for admin routes — DB-backed persistence for cost config + events."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src.api.app import create_app
from src.api.deps import get_current_user, get_db
from src.models.admin_event import AdminEvent
from src.models.system_config import SystemConfig
from src.models.user import User, UserRole


def _make_user(role: UserRole = UserRole.admin, **overrides) -> MagicMock:
    defaults = {
        "id": uuid.uuid4(),
        "name": "Admin User",
        "email": "admin@example.com",
        "role": role,
        "password_hash": "hashed",
        "created_at": datetime.now(UTC),
        "updated_at": None,
    }
    defaults.update(overrides)
    user = MagicMock(spec=User)
    for k, v in defaults.items():
        setattr(user, k, v)
    return user


class _FakeSession:
    """Minimal AsyncSession stand-in that records add()/execute() calls."""

    def __init__(self) -> None:
        self.added: list = []
        self.executed: list = []

    def add(self, obj) -> None:
        self.added.append(obj)

    async def execute(self, stmt):
        self.executed.append(stmt)
        return MagicMock(scalar_one_or_none=MagicMock(return_value=None))

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _app_with_overrides(user, session):
    app = create_app()

    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db
    app.dependency_overrides[get_current_user] = lambda: user
    return app


async def test_refresh_vector_store_inserts_admin_event():
    user = _make_user()
    session = _FakeSession()
    app = _app_with_overrides(user, session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/v1/admin/vector-stores/refresh", json={"store": "vs_test"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["store"] == "vs_test"
    # One AdminEvent added to the session with the right shape.
    events = [x for x in session.added if isinstance(x, AdminEvent)]
    assert len(events) == 1
    event = events[0]
    assert event.actor_id == user.id
    assert event.action == "vector_store_refresh_requested"
    assert event.payload == {"store": "vs_test"}


async def test_refresh_vector_store_forbidden_for_non_admin():
    clerk = _make_user(role=UserRole.judge)
    session = _FakeSession()
    app = _app_with_overrides(clerk, session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/v1/admin/vector-stores/refresh", json={"store": "vs_x"})

    assert resp.status_code == 403
    assert session.added == []


async def test_set_cost_config_issues_upsert_on_system_config():
    user = _make_user()
    session = _FakeSession()
    app = _app_with_overrides(user, session)

    payload = {
        "prompt_cost_per_1k": 0.01,
        "completion_cost_per_1k": 0.03,
        "currency": "USD",
        "budget_daily": 100.0,
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/v1/admin/cost-config", json=payload)

    assert resp.status_code == 200
    body = resp.json()
    assert body["config"] == payload
    # Exactly one execute() call, and it is an on-conflict-do-update against system_config.
    assert len(session.executed) == 1
    stmt = session.executed[0]
    compiled = str(stmt)
    assert "system_config" in compiled
    assert "ON CONFLICT" in compiled.upper() or "on conflict" in compiled.lower()


async def test_set_cost_config_forbidden_for_non_admin():
    clerk = _make_user(role=UserRole.judge)
    session = _FakeSession()
    app = _app_with_overrides(clerk, session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/v1/admin/cost-config", json={"currency": "USD"})

    assert resp.status_code == 403
    assert session.executed == []


# Sanity check: the new ORM models are importable and well-formed.
def test_admin_event_model_has_expected_columns():
    cols = {c.name for c in AdminEvent.__table__.columns}
    assert {"id", "actor_id", "action", "payload", "created_at"}.issubset(cols)


def test_system_config_model_has_expected_columns():
    cols = {c.name for c in SystemConfig.__table__.columns}
    assert {"key", "value", "updated_by", "updated_at"}.issubset(cols)
    # key is the primary key.
    pk_names = {c.name for c in SystemConfig.__table__.primary_key.columns}
    assert pk_names == {"key"}


def test_system_config_select_compiles():
    # Smoke test: SQLAlchemy can compile a SELECT against the mapped table.
    stmt = select(SystemConfig).where(SystemConfig.key == "cost_config")
    assert "system_config" in str(stmt)
