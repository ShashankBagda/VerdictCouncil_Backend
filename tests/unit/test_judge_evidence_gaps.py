"""Unit tests for GET /cases/{case_id}/evidence-gaps (US-010)."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.deps import get_current_user, get_db
from src.models.case import (
    Case,
    Evidence,
    EvidenceStrength,
    EvidenceType,
    Fact,
    FactConfidence,
    FactStatus,
)
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


def _make_case(case_id: uuid.UUID) -> MagicMock:
    case = MagicMock(spec=Case)
    case.id = case_id
    return case


def _make_evidence(case_id: uuid.UUID, strength: EvidenceStrength) -> MagicMock:
    ev = MagicMock(spec=Evidence)
    ev.id = uuid.uuid4()
    ev.case_id = case_id
    ev.evidence_type = EvidenceType.documentary
    ev.strength = strength
    ev.admissibility_flags = None
    ev.linked_claims = None
    return ev


def _make_fact(case_id: uuid.UUID, corroboration=None) -> MagicMock:
    f = MagicMock(spec=Fact)
    f.id = uuid.uuid4()
    f.case_id = case_id
    f.description = "A disputed fact"
    f.confidence = FactConfidence.low
    f.status = FactStatus.agreed
    f.corroboration = corroboration
    return f


def _build_mock_session():
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


def _scalars_result(items):
    scalars = MagicMock()
    scalars.all.return_value = items
    result = MagicMock()
    result.scalars.return_value = scalars
    return result


def _scalar_one_result(value):
    result = MagicMock()
    result.scalar_one.return_value = value
    return result


def _scalar_one_or_none_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _app_with_overrides(mock_db, mock_user):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_current_user] = lambda: mock_user
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_evidence_gaps_returns_weak_and_uncorroborated():
    case_id = uuid.uuid4()
    user = _make_user()
    case = _make_case(case_id)
    weak_ev = _make_evidence(case_id, EvidenceStrength.weak)
    uncorroborated_fact = _make_fact(case_id, corroboration=None)

    mock_db = _build_mock_session()
    mock_db.execute.side_effect = [
        _scalar_one_or_none_result(case),  # case existence check
        _scalars_result([weak_ev]),  # weak evidence query
        _scalars_result([uncorroborated_fact]),  # uncorroborated facts query
        _scalar_one_result(3),  # total evidence count
        _scalar_one_result(5),  # total fact count
    ]

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/cases/{case_id}/evidence-gaps")

    assert resp.status_code == 200
    data = resp.json()
    assert data["case_id"] == str(case_id)
    assert len(data["weak_evidence"]) == 1
    assert len(data["uncorroborated_facts"]) == 1
    assert data["total_evidence_count"] == 3
    assert data["total_fact_count"] == 5
    assert "1 of 3" in data["gap_summary"]
    assert "1 of 5" in data["gap_summary"]


async def test_evidence_gaps_case_not_found():
    case_id = uuid.uuid4()
    user = _make_user()

    mock_db = _build_mock_session()
    mock_db.execute.return_value = _scalar_one_or_none_result(None)

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/cases/{case_id}/evidence-gaps")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Case not found"


async def test_evidence_gaps_no_gaps():
    case_id = uuid.uuid4()
    user = _make_user()
    case = _make_case(case_id)

    mock_db = _build_mock_session()
    mock_db.execute.side_effect = [
        _scalar_one_or_none_result(case),
        _scalars_result([]),
        _scalars_result([]),
        _scalar_one_result(4),
        _scalar_one_result(4),
    ]

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/cases/{case_id}/evidence-gaps")

    assert resp.status_code == 200
    data = resp.json()
    assert data["weak_evidence"] == []
    assert data["uncorroborated_facts"] == []
    assert "0 of 4" in data["gap_summary"]


async def test_evidence_gaps_non_judge_forbidden():
    case_id = uuid.uuid4()
    clerk = _make_user(role=UserRole.clerk)
    mock_db = _build_mock_session()

    app = _app_with_overrides(mock_db, clerk)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/cases/{case_id}/evidence-gaps")

    assert resp.status_code == 403
