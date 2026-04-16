"""Unit tests for GET /cases/{case_id}/jurisdiction (US-003)."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.deps import get_current_user, get_db
from src.models.audit import AuditLog
from src.models.case import Case
from src.models.user import User, UserRole

# ---------------------------------------------------------------------------
# Helpers (mirror tests/unit/test_judge_evidence_gaps.py patterns)
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


def _make_case(case_id: uuid.UUID, jurisdiction_valid: bool | None = None) -> MagicMock:
    case = MagicMock(spec=Case)
    case.id = case_id
    case.jurisdiction_valid = jurisdiction_valid
    return case


def _make_audit(
    case_id: uuid.UUID, payload: dict, agent_name: str = "case-processing"
) -> MagicMock:
    entry = MagicMock(spec=AuditLog)
    entry.id = uuid.uuid4()
    entry.case_id = case_id
    entry.agent_name = agent_name
    entry.action = "agent_response"
    entry.output_payload = payload
    entry.created_at = datetime.now(UTC)
    return entry


def _scalar_one_or_none_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _build_mock_session():
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


def _app_with_overrides(mock_db, mock_user):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_current_user] = lambda: mock_user
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_jurisdiction_returns_case_flag_and_audit_payload():
    case_id = uuid.uuid4()
    user = _make_user()
    case = _make_case(case_id, jurisdiction_valid=True)
    payload = {
        "jurisdiction_valid": True,
        "jurisdiction_issues": [],
        "domain": "small_claims",
    }
    audit = _make_audit(case_id, payload)

    mock_db = _build_mock_session()
    mock_db.execute.side_effect = [
        _scalar_one_or_none_result(case),
        _scalar_one_or_none_result(audit),
    ]

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/cases/{case_id}/jurisdiction")

    assert resp.status_code == 200
    data = resp.json()
    assert data["case_id"] == str(case_id)
    assert data["jurisdiction_valid"] is True
    assert data["jurisdiction_issues"] == []
    assert data["audit_payload"]["domain"] == "small_claims"
    assert data["audit_log_id"] == str(audit.id)
    assert data["has_validation_data"] is True


async def test_jurisdiction_extracts_issues_from_case_metadata():
    """Issues nested under case_metadata should still surface."""
    case_id = uuid.uuid4()
    user = _make_user()
    case = _make_case(case_id, jurisdiction_valid=False)
    payload = {
        "case_metadata": {
            "jurisdiction_issues": [
                "Claim amount $25,000 exceeds SCT $20,000 limit",
            ],
        },
    }
    audit = _make_audit(case_id, payload)

    mock_db = _build_mock_session()
    mock_db.execute.side_effect = [
        _scalar_one_or_none_result(case),
        _scalar_one_or_none_result(audit),
    ]

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/cases/{case_id}/jurisdiction")

    assert resp.status_code == 200
    data = resp.json()
    assert data["jurisdiction_valid"] is False
    assert data["jurisdiction_issues"] == [
        "Claim amount $25,000 exceeds SCT $20,000 limit"
    ]
    assert data["has_validation_data"] is True


async def test_jurisdiction_no_audit_falls_back_to_case_flag():
    """Endpoint still works before any case-processing audit row is written."""
    case_id = uuid.uuid4()
    user = _make_user()
    case = _make_case(case_id, jurisdiction_valid=True)

    mock_db = _build_mock_session()
    mock_db.execute.side_effect = [
        _scalar_one_or_none_result(case),
        _scalar_one_or_none_result(None),
    ]

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/cases/{case_id}/jurisdiction")

    assert resp.status_code == 200
    data = resp.json()
    assert data["jurisdiction_valid"] is True
    assert data["jurisdiction_issues"] == []
    assert data["audit_payload"] is None
    assert data["audit_log_id"] is None
    assert data["has_validation_data"] is True


async def test_jurisdiction_pending_case_has_no_validation_data():
    """A freshly-created case with no flag and no audit returns has_validation_data=False."""
    case_id = uuid.uuid4()
    user = _make_user()
    case = _make_case(case_id, jurisdiction_valid=None)

    mock_db = _build_mock_session()
    mock_db.execute.side_effect = [
        _scalar_one_or_none_result(case),
        _scalar_one_or_none_result(None),
    ]

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/cases/{case_id}/jurisdiction")

    assert resp.status_code == 200
    data = resp.json()
    assert data["jurisdiction_valid"] is None
    assert data["has_validation_data"] is False


async def test_jurisdiction_case_not_found():
    case_id = uuid.uuid4()
    user = _make_user()

    mock_db = _build_mock_session()
    mock_db.execute.return_value = _scalar_one_or_none_result(None)

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/cases/{case_id}/jurisdiction")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Case not found"


async def test_jurisdiction_non_judge_forbidden():
    case_id = uuid.uuid4()
    clerk = _make_user(role=UserRole.clerk)
    mock_db = _build_mock_session()

    app = _app_with_overrides(mock_db, clerk)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/cases/{case_id}/jurisdiction")

    assert resp.status_code == 403
