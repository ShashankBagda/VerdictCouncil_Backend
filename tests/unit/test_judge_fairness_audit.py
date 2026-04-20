"""Unit tests for GET /cases/{case_id}/fairness-audit (US-023)."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.deps import get_current_user, get_db
from src.models.audit import AuditLog
from src.models.case import Case, Verdict
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


def _make_verdict(case_id: uuid.UUID, fairness_report=None) -> MagicMock:
    v = MagicMock(spec=Verdict)
    v.id = uuid.uuid4()
    v.case_id = case_id
    v.fairness_report = fairness_report
    return v


def _make_audit_log(case_id: uuid.UUID, output_payload=None) -> MagicMock:
    log = MagicMock(spec=AuditLog)
    log.id = uuid.uuid4()
    log.case_id = case_id
    log.agent_name = "governance-verdict"
    log.action = "governance_check"
    log.output_payload = output_payload
    log.created_at = datetime.now(UTC)
    return log


def _build_mock_session():
    session = AsyncMock()
    session.execute = AsyncMock()
    return session


def _scalar_one_or_none_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
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
# Tests
# ---------------------------------------------------------------------------


async def test_fairness_audit_with_all_data():
    case_id = uuid.uuid4()
    user = _make_user()
    case = MagicMock(spec=Case)
    case.id = case_id

    fairness_report = {"audit_passed": True, "critical_issues_found": False}
    verdict = _make_verdict(case_id, fairness_report=fairness_report)
    audit_log = _make_audit_log(case_id, output_payload={"fairness_check": {"audit_passed": True}})

    mock_db = _build_mock_session()
    mock_db.execute.side_effect = [
        _scalar_one_or_none_result(case),
        _scalar_one_or_none_result(verdict),
        _scalars_result([audit_log]),
    ]

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/cases/{case_id}/fairness-audit")

    assert resp.status_code == 200
    data = resp.json()
    assert data["case_id"] == str(case_id)
    assert data["has_fairness_data"] is True
    assert data["verdict_fairness_report"]["audit_passed"] is True
    assert len(data["governance_checks"]) == 1
    assert data["governance_checks"][0]["action"] == "governance_check"


async def test_fairness_audit_no_verdict():
    case_id = uuid.uuid4()
    user = _make_user()
    case = MagicMock(spec=Case)
    case.id = case_id

    mock_db = _build_mock_session()
    mock_db.execute.side_effect = [
        _scalar_one_or_none_result(case),
        _scalar_one_or_none_result(None),
        _scalars_result([]),
    ]

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/cases/{case_id}/fairness-audit")

    assert resp.status_code == 200
    data = resp.json()
    assert data["has_fairness_data"] is False
    assert data["verdict_fairness_report"] is None
    assert data["governance_checks"] == []


async def test_fairness_audit_case_not_found():
    case_id = uuid.uuid4()
    user = _make_user()

    mock_db = _build_mock_session()
    mock_db.execute.return_value = _scalar_one_or_none_result(None)

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/cases/{case_id}/fairness-audit")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Case not found"


async def test_fairness_audit_non_judge_forbidden():
    case_id = uuid.uuid4()
    clerk = _make_user(role=UserRole.clerk)
    mock_db = _build_mock_session()

    app = _app_with_overrides(mock_db, clerk)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/cases/{case_id}/fairness-audit")

    assert resp.status_code == 403


async def test_fairness_audit_no_fairness_report_in_verdict():
    case_id = uuid.uuid4()
    user = _make_user()
    case = MagicMock(spec=Case)
    case.id = case_id

    verdict = _make_verdict(case_id, fairness_report=None)
    audit_log = _make_audit_log(case_id, output_payload={"fairness_check": {}})

    mock_db = _build_mock_session()
    mock_db.execute.side_effect = [
        _scalar_one_or_none_result(case),
        _scalar_one_or_none_result(verdict),
        _scalars_result([audit_log]),
    ]

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/cases/{case_id}/fairness-audit")

    assert resp.status_code == 200
    data = resp.json()
    assert data["verdict_fairness_report"] is None
    assert data["has_fairness_data"] is True  # governance audit log exists
