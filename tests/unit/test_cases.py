"""Unit tests for case CRUD endpoints (POST /, GET /, GET /{id})."""

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
        "name": "Judge Dredd",
        "email": "dredd@example.com",
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


def _make_case(created_by: uuid.UUID, **overrides) -> MagicMock:
    defaults = {
        "id": uuid.uuid4(),
        "domain": CaseDomain.traffic_violation,
        "status": CaseStatus.pending,
        "jurisdiction_valid": True,
        "complexity": None,
        "route": None,
        "created_by": created_by,
        "created_at": datetime.now(UTC),
        "updated_at": None,
        "parties": [],
        "documents": [],
        "evidence": [],
        "facts": [],
        "witnesses": [],
        "legal_rules": [],
        "precedents": [],
        "arguments": [],
        "deliberations": [],
        "verdicts": [],
        "audit_logs": [],
    }
    defaults.update(overrides)
    case = MagicMock(spec=Case)
    for k, v in defaults.items():
        setattr(case, k, v)
    return case


def _mock_scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _mock_scalars_result(values: list):
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = values
    result.scalars.return_value = scalars
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


class TestCreateCase:
    async def test_create_case(self):
        """POST /api/v1/cases/ with valid data returns 201."""
        user = _make_user()
        mock_db = _build_mock_session()

        case_id = uuid.uuid4()
        now = datetime.now(UTC)

        async def _refresh(case):
            case.id = case_id
            case.status = CaseStatus.pending
            case.created_at = now
            case.updated_at = None

        mock_db.refresh.side_effect = _refresh

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/cases/",
                json={"domain": "traffic_violation"},
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["domain"] == "traffic_violation"
        assert data["status"] == "pending"
        mock_db.add.assert_called_once()


class TestListCases:
    async def test_list_cases_empty(self):
        """GET /api/v1/cases/ with no cases returns 200 with empty list."""
        user = _make_user()
        mock_db = _build_mock_session()
        mock_db.execute.return_value = _mock_scalars_result([])

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/cases/")

        assert resp.status_code == 200
        data = resp.json()
        # Response can be a list or a paginated dict with an items key
        if isinstance(data, list):
            assert data == []
        else:
            items = data.get("items", data.get("cases", []))
            assert items == []

    async def test_list_cases_with_data(self):
        """GET /api/v1/cases/ returns existing cases."""
        user = _make_user()
        mock_db = _build_mock_session()

        cases = [
            _make_case(user.id, domain=CaseDomain.traffic_violation),
            _make_case(user.id, domain=CaseDomain.small_claims),
        ]
        mock_db.execute.return_value = _mock_scalars_result(cases)

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/cases/")

        assert resp.status_code == 200
        data = resp.json()
        items = data if isinstance(data, list) else data.get("items", data.get("cases", []))
        assert len(items) == 2


class TestGetCaseDetail:
    async def test_get_case_detail(self):
        """GET /api/v1/cases/{id} returns 200 with case data."""
        user = _make_user()
        mock_db = _build_mock_session()

        case = _make_case(user.id)
        mock_db.execute.return_value = _mock_scalar_result(case)

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/cases/{case.id}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(case.id)
        assert data["domain"] == "traffic_violation"

    async def test_get_case_not_found(self):
        """GET /api/v1/cases/{nonexistent_id} returns 404."""
        user = _make_user()
        mock_db = _build_mock_session()
        mock_db.execute.return_value = _mock_scalar_result(None)

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        fake_id = uuid.uuid4()
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/cases/{fake_id}")

        assert resp.status_code == 404


class TestCaseOwnership:
    async def test_case_ownership_enforcement(self):
        """A clerk should not be able to access another user's case."""
        user_a = _make_user(role=UserRole.clerk, email="clerk_a@example.com")
        user_b = _make_user(role=UserRole.clerk, email="clerk_b@example.com")

        # Case belongs to user_b
        case = _make_case(user_b.id)

        mock_db = _build_mock_session()
        mock_db.execute.return_value = _mock_scalar_result(case)

        # Authenticated as user_a (clerk)
        app = _app_with_overrides(mock_db, user_a)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/cases/{case.id}")

        # The endpoint should return 403 or 404 for cases not owned by the clerk.
        # Accept either — 404 hides existence, 403 is explicit.
        assert resp.status_code in (403, 404)
