"""Unit tests for GET /cases/{case_id}/evidence-dashboard (US-006)."""

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


def _make_evidence(
    case_id: uuid.UUID,
    strength: EvidenceStrength | None,
    flags: dict | None = None,
) -> MagicMock:
    ev = MagicMock(spec=Evidence)
    ev.id = uuid.uuid4()
    ev.case_id = case_id
    ev.evidence_type = EvidenceType.documentary
    ev.strength = strength
    ev.admissibility_flags = flags
    ev.linked_claims = None
    return ev


def _make_fact(
    case_id: uuid.UUID,
    *,
    status: FactStatus | None = FactStatus.agreed,
    corroboration: dict | None = None,
    description: str = "Some fact",
) -> MagicMock:
    f = MagicMock(spec=Fact)
    f.id = uuid.uuid4()
    f.case_id = case_id
    f.description = description
    f.confidence = FactConfidence.medium
    f.status = status
    f.corroboration = corroboration
    return f


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


async def test_dashboard_aggregates_strength_admissibility_and_contradictions():
    case_id = uuid.uuid4()
    user = _make_user()
    case = _make_case(case_id)

    evidence_rows = [
        _make_evidence(case_id, EvidenceStrength.strong, {"authenticated": True}),
        _make_evidence(case_id, EvidenceStrength.strong, {"authenticated": True, "hearsay": False}),
        _make_evidence(case_id, EvidenceStrength.moderate, {"authenticated": True}),
        _make_evidence(case_id, EvidenceStrength.weak, {"authenticated": False, "hearsay": True}),
        _make_evidence(case_id, None, None),
    ]
    fact_rows = [
        _make_fact(
            case_id,
            status=FactStatus.disputed,
            corroboration={"dispute_reason": "Witness recanted"},
            description="Disputed fact",
        ),
        _make_fact(
            case_id,
            status=FactStatus.agreed,
            corroboration={"contradicts": ["fact-99"]},
            description="Conflict via corroboration",
        ),
        _make_fact(case_id, status=FactStatus.agreed, corroboration=None, description="Quiet"),
    ]

    mock_db = _build_mock_session()
    mock_db.execute.side_effect = [
        _scalar_one_or_none_result(case),
        _scalars_result(evidence_rows),
        _scalars_result(fact_rows),
    ]

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/cases/{case_id}/evidence-dashboard")

    assert resp.status_code == 200
    data = resp.json()
    strength = data["strength_summary"]
    assert strength["strong"] == 2
    assert strength["moderate"] == 1
    assert strength["weak"] == 1
    assert strength["unrated"] == 1
    assert strength["total"] == 5

    flags = {item["flag"]: item for item in data["admissibility_flags_summary"]}
    assert flags["authenticated"]["truthy_count"] == 3
    assert flags["authenticated"]["falsy_count"] == 1
    assert flags["hearsay"]["truthy_count"] == 1
    assert flags["hearsay"]["falsy_count"] == 1

    assert len(data["contradictions"]) == 2
    descriptions = {c["description"] for c in data["contradictions"]}
    assert descriptions == {"Disputed fact", "Conflict via corroboration"}

    assert data["total_evidence_count"] == 5
    assert data["total_fact_count"] == 3
    assert data["has_evidence_data"] is True


async def test_dashboard_empty_case_returns_zeroed_summary():
    case_id = uuid.uuid4()
    user = _make_user()
    case = _make_case(case_id)

    mock_db = _build_mock_session()
    mock_db.execute.side_effect = [
        _scalar_one_or_none_result(case),
        _scalars_result([]),
        _scalars_result([]),
    ]

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/cases/{case_id}/evidence-dashboard")

    assert resp.status_code == 200
    data = resp.json()
    assert data["strength_summary"]["total"] == 0
    assert data["admissibility_flags_summary"] == []
    assert data["contradictions"] == []
    assert data["has_evidence_data"] is False


async def test_dashboard_case_not_found():
    case_id = uuid.uuid4()
    user = _make_user()

    mock_db = _build_mock_session()
    mock_db.execute.return_value = _scalar_one_or_none_result(None)

    app = _app_with_overrides(mock_db, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/cases/{case_id}/evidence-dashboard")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Case not found"


async def test_dashboard_non_judge_forbidden():
    case_id = uuid.uuid4()
    clerk = _make_user(role=UserRole.clerk)
    mock_db = _build_mock_session()

    app = _app_with_overrides(mock_db, clerk)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/cases/{case_id}/evidence-dashboard")

    assert resp.status_code == 403
