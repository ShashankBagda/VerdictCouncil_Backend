"""Unit tests for GET /escalated-cases/ and POST /escalated-cases/{id}/action (US-024)."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.deps import get_current_user, get_db
from src.models.case import Case, CaseDomain, CaseStatus
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


def _make_case(status: CaseStatus = CaseStatus.escalated, **overrides) -> MagicMock:
    defaults = {
        "id": uuid.uuid4(),
        "domain": CaseDomain.traffic_violation,
        "description": "A complex case",
        "status": status,
        "route": "escalate_human",
        "complexity": "high",
        "created_by": uuid.uuid4(),
        "created_at": datetime.now(UTC),
        "updated_at": None,
    }
    defaults.update(overrides)
    case = MagicMock(spec=Case)
    for k, v in defaults.items():
        setattr(case, k, v)
    return case


def _build_mock_session():
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


def _scalar_one_or_none_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _scalar_one_result(value):
    result = MagicMock()
    result.scalar_one.return_value = value
    return result


def _scalars_result(items):
    scalars = MagicMock()
    scalars.all.return_value = items
    result = MagicMock()
    result.scalars.return_value = scalars
    return result


def _app_with_overrides(mock_db, mock_user):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_current_user] = lambda: mock_user
    return app


# ---------------------------------------------------------------------------
# List escalated cases
# ---------------------------------------------------------------------------


async def test_list_escalated_cases_success():
    user = _make_user()
    case1 = _make_case()
    case2 = _make_case()

    mock_db = _build_mock_session()
    mock_db.execute.side_effect = [
        _scalar_one_result(2),  # count query
        _scalars_result([case1, case2]),  # page query
    ]

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/escalated-cases/")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["per_page"] == 20


async def test_list_escalated_cases_empty():
    user = _make_user()

    mock_db = _build_mock_session()
    mock_db.execute.side_effect = [
        _scalar_one_result(0),
        _scalars_result([]),
    ]

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/escalated-cases/")

    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    assert resp.json()["items"] == []


async def test_list_escalated_cases_non_judge_forbidden():
    clerk = _make_user(role=UserRole.clerk)
    mock_db = _build_mock_session()

    app = _app_with_overrides(mock_db, clerk)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/escalated-cases/")

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Take escalation action
# ---------------------------------------------------------------------------


async def test_escalation_action_add_notes():
    case_id = uuid.uuid4()
    user = _make_user()
    case = _make_case(id=case_id)

    mock_db = _build_mock_session()
    mock_db.execute.return_value = _scalar_one_or_none_result(case)

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/escalated-cases/{case_id}/action",
            json={"action": "add_notes", "notes": "Reviewed and noted."},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "add_notes"
    assert data["new_status"] == "escalated"
    assert "Notes recorded" in data["message"]


async def test_escalation_action_return_to_pipeline():
    case_id = uuid.uuid4()
    user = _make_user()
    case = _make_case(id=case_id)

    mock_db = _build_mock_session()
    mock_db.execute.return_value = _scalar_one_or_none_result(case)

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/escalated-cases/{case_id}/action",
            json={"action": "return_to_pipeline"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["new_status"] == "processing"
    assert case.status == CaseStatus.processing


async def test_escalation_action_case_not_found():
    case_id = uuid.uuid4()
    user = _make_user()

    mock_db = _build_mock_session()
    mock_db.execute.return_value = _scalar_one_or_none_result(None)

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/escalated-cases/{case_id}/action",
            json={"action": "close"},
        )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Case not found"


async def test_escalation_action_case_not_escalated():
    case_id = uuid.uuid4()
    user = _make_user()
    case = _make_case(id=case_id, status=CaseStatus.ready_for_review)

    mock_db = _build_mock_session()
    mock_db.execute.return_value = _scalar_one_or_none_result(case)

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/escalated-cases/{case_id}/action",
            json={"action": "close"},
        )

    assert resp.status_code == 400
    assert "not in escalated status" in resp.json()["detail"]


async def test_escalation_action_non_judge_forbidden():
    case_id = uuid.uuid4()
    clerk = _make_user(role=UserRole.clerk)
    mock_db = _build_mock_session()

    app = _app_with_overrides(mock_db, clerk)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/escalated-cases/{case_id}/action",
            json={"action": "close"},
        )

    assert resp.status_code == 403
