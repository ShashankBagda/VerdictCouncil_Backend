"""Unit tests for PATCH /cases/{case_id}/facts/{fact_id}/dispute (US-009)."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.deps import get_current_user, get_db
from src.models.case import Fact, FactConfidence, FactStatus
from src.models.user import User, UserRole

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(**overrides) -> MagicMock:
    defaults = {
        "id": uuid.uuid4(),
        "name": "Justice Bao",
        "email": "bao@example.com",
        "role": UserRole.judge,
        "password_hash": "hashed",
        "created_at": datetime.now(UTC),
        "updated_at": None,
    }
    defaults.update(overrides)
    user = MagicMock(spec=User)
    for k, v in defaults.items():
        setattr(user, k, v)
    return user


def _make_fact(case_id: uuid.UUID, **overrides) -> MagicMock:
    defaults = {
        "id": uuid.uuid4(),
        "case_id": case_id,
        "description": "The vehicle collided at 3pm",
        "status": FactStatus.agreed,
        "confidence": FactConfidence.high,
        "corroboration": None,
    }
    defaults.update(overrides)
    fact = MagicMock(spec=Fact)
    for k, v in defaults.items():
        setattr(fact, k, v)
    return fact


def _mock_scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _build_mock_session():
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


def _app_with_overrides(mock_db, mock_user):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_current_user] = lambda: mock_user
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_dispute_fact_success():
    case_id = uuid.uuid4()
    fact_id = uuid.uuid4()
    user = _make_user()
    fact = _make_fact(case_id=case_id, id=fact_id)

    mock_db = _build_mock_session()
    mock_db.execute.return_value = _mock_scalar_result(fact)

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            f"/api/v1/cases/{case_id}/facts/{fact_id}/dispute",
            json={"reason": "Witness accounts conflict with this fact."},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["fact_id"] == str(fact_id)
    assert data["case_id"] == str(case_id)
    assert data["status"] == "disputed"
    assert data["confidence"] == "disputed"
    assert data["reason"] == "Witness accounts conflict with this fact."
    # Verify model mutation
    assert fact.status == FactStatus.disputed
    assert fact.confidence == FactConfidence.disputed
    assert mock_db.add.called


async def test_dispute_fact_not_found():
    case_id = uuid.uuid4()
    fact_id = uuid.uuid4()
    user = _make_user()

    mock_db = _build_mock_session()
    mock_db.execute.return_value = _mock_scalar_result(None)

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            f"/api/v1/cases/{case_id}/facts/{fact_id}/dispute",
            json={"reason": "Conflict."},
        )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Fact not found"


async def test_dispute_fact_non_judge_forbidden():
    case_id = uuid.uuid4()
    fact_id = uuid.uuid4()
    clerk = _make_user(role=UserRole.clerk)

    mock_db = _build_mock_session()
    app = _app_with_overrides(mock_db, clerk)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            f"/api/v1/cases/{case_id}/facts/{fact_id}/dispute",
            json={"reason": "Conflict."},
        )

    assert resp.status_code == 403


async def test_dispute_fact_reason_too_long():
    case_id = uuid.uuid4()
    fact_id = uuid.uuid4()
    user = _make_user()
    mock_db = _build_mock_session()

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            f"/api/v1/cases/{case_id}/facts/{fact_id}/dispute",
            json={"reason": "x" * 1001},
        )

    assert resp.status_code == 422


async def test_dispute_fact_already_disputed_returns_409():
    case_id = uuid.uuid4()
    fact_id = uuid.uuid4()
    user = _make_user()
    fact = _make_fact(case_id=case_id, id=fact_id, status=FactStatus.disputed)

    mock_db = _build_mock_session()
    mock_db.execute.return_value = _mock_scalar_result(fact)

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            f"/api/v1/cases/{case_id}/facts/{fact_id}/dispute",
            json={"reason": "Attempting to re-dispute."},
        )

    assert resp.status_code == 409
    assert resp.json()["detail"] == "Fact is already disputed."


async def test_dispute_fact_preserves_existing_corroboration():
    case_id = uuid.uuid4()
    fact_id = uuid.uuid4()
    user = _make_user()
    fact = _make_fact(case_id=case_id, id=fact_id, corroboration={"source": "witness_A"})

    mock_db = _build_mock_session()
    mock_db.execute.return_value = _mock_scalar_result(fact)

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.patch(
            f"/api/v1/cases/{case_id}/facts/{fact_id}/dispute",
            json={"reason": "Dispute reason."},
        )

    assert fact.corroboration["source"] == "witness_A"
    assert fact.corroboration["dispute_reason"] == "Dispute reason."
