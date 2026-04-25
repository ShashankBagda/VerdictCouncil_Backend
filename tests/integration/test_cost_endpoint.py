"""Sprint 4 4.C4.4 + 4.C4.5 — /cost/summary endpoint + Prometheus gauge.

Mocks the DB layer so the test stays unit-grade fast while still
exercising the full FastAPI stack (auth, query construction, gauge
side-effect).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import app
from src.api.deps import get_current_user, get_db
from src.api.middleware.metrics import metrics_store
from src.models.user import User, UserRole

JUDGE_ID = uuid.uuid4()
CASE_ID = uuid.uuid4()


def _make_auth(role: UserRole = UserRole.judge, user_id: uuid.UUID = JUDGE_ID):
    user = MagicMock(spec=User)
    user.id = user_id
    user.role = role

    async def _override():
        return user

    return _override


def _override_db_with_total(total_usd: Decimal, audit_count: int, *, case=None):
    """Yield a session whose .execute returns (sum, count) for cost queries."""

    async def _gen():
        cost_row = MagicMock()
        cost_row.one = MagicMock(return_value=(total_usd, audit_count))

        case_result = MagicMock()
        case_result.scalar_one_or_none = MagicMock(return_value=case)

        execute_calls: list = []

        async def _execute(stmt, *_args, **_kwargs):
            execute_calls.append(stmt)
            # The route runs at most one Case lookup before the cost query.
            # First call returns the case lookup result; subsequent calls
            # return the cost row.
            stmt_str = str(stmt).lower()
            if "from cases" in stmt_str and "sum" not in stmt_str:
                return case_result
            return cost_row

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_execute)
        yield session

    return _gen


@pytest.fixture(autouse=True)
def _reset_overrides():
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_summary_global_judge_scope() -> None:
    app.dependency_overrides[get_current_user] = _make_auth()
    app.dependency_overrides[get_db] = _override_db_with_total(Decimal("1.234567"), 42)

    async with _client() as c:
        r = await c.get("/api/v1/cost/summary")

    assert r.status_code == 200
    data = r.json()
    assert Decimal(data["total_usd"]) == Decimal("1.234567")
    assert data["audit_row_count"] == 42
    assert data["case_id"] is None


@pytest.mark.asyncio
async def test_cost_summary_per_case_updates_prometheus_gauge() -> None:
    case = MagicMock()
    case.id = CASE_ID
    case.created_by = JUDGE_ID

    app.dependency_overrides[get_current_user] = _make_auth()
    app.dependency_overrides[get_db] = _override_db_with_total(
        Decimal("0.500000"), 7, case=case
    )

    metrics_store._case_cost_usd.clear()

    async with _client() as c:
        r = await c.get(f"/api/v1/cost/summary?case_id={CASE_ID}")

    assert r.status_code == 200
    data = r.json()
    assert data["case_id"] == str(CASE_ID)
    assert Decimal(data["total_usd"]) == Decimal("0.500000")

    rendered = metrics_store.render()
    assert "verdict_council_case_cost_usd" in rendered
    assert str(CASE_ID) in rendered


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_summary_judge_cannot_query_other_judges_case() -> None:
    other = uuid.uuid4()
    case = MagicMock()
    case.id = CASE_ID
    case.created_by = other  # owned by someone else

    app.dependency_overrides[get_current_user] = _make_auth(user_id=JUDGE_ID)
    app.dependency_overrides[get_db] = _override_db_with_total(
        Decimal("99"), 1, case=case
    )

    async with _client() as c:
        r = await c.get(f"/api/v1/cost/summary?case_id={CASE_ID}")

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_cost_summary_404_on_missing_case() -> None:
    app.dependency_overrides[get_current_user] = _make_auth()
    app.dependency_overrides[get_db] = _override_db_with_total(
        Decimal("0"), 0, case=None
    )

    async with _client() as c:
        r = await c.get(f"/api/v1/cost/summary?case_id={CASE_ID}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_cost_summary_admin_sees_other_case() -> None:
    other = uuid.uuid4()
    case = MagicMock()
    case.id = CASE_ID
    case.created_by = other

    app.dependency_overrides[get_current_user] = _make_auth(role=UserRole.admin)
    app.dependency_overrides[get_db] = _override_db_with_total(
        Decimal("2.000000"), 3, case=case
    )

    async with _client() as c:
        r = await c.get(f"/api/v1/cost/summary?case_id={CASE_ID}")

    assert r.status_code == 200
    assert Decimal(r.json()["total_usd"]) == Decimal("2.000000")
