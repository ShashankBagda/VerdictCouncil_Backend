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
        "title": "Traffic light prosecution",
        "description": "Traffic prosecution arising from an alleged red-light offence.",
        "filed_date": datetime.now(UTC).date(),
        "claim_amount": None,
        "consent_to_higher_claim_limit": False,
        "offence_code": "RTA-S64",
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
        "hearing_analyses": [],
        "reopen_requests": [],
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


def _mock_count_result(value: int):
    result = MagicMock()
    result.scalar_one.return_value = value
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
                json={
                    "domain": "traffic_violation",
                    "title": "Traffic prosecution",
                    "description": "Alleged red-light offence at Orchard Road junction.",
                    "filed_date": "2026-04-20",
                    "parties": [
                        {"name": "Public Prosecutor", "role": "prosecution"},
                        {"name": "John Tan", "role": "accused"},
                    ],
                    "offence_code": "RTA-S64",
                },
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["domain"] == "traffic_violation"
        assert data["title"] == "Traffic prosecution"
        assert data["status"] == "pending"
        assert data["status_group"] == "processing"
        mock_db.add.assert_called_once()

    async def test_create_case_rejects_small_claims_without_claim_amount(self):
        user = _make_user()
        mock_db = _build_mock_session()

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/cases/",
                json={
                    "domain": "small_claims",
                    "title": "Defective furniture claim",
                    "description": "Claimant alleges delivered goods were defective.",
                    "filed_date": "2026-04-20",
                    "parties": [
                        {"name": "Lim", "role": "claimant"},
                        {"name": "FurniturePlus", "role": "respondent"},
                    ],
                },
            )

        assert resp.status_code == 422


class TestListCases:
    async def test_list_cases_empty(self):
        """GET /api/v1/cases/ with no cases returns 200 with empty list."""
        user = _make_user()
        mock_db = _build_mock_session()
        mock_db.execute.side_effect = [_mock_count_result(0), _mock_scalars_result([])]

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/cases/")

        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    async def test_list_cases_with_data(self):
        """GET /api/v1/cases/ returns existing cases."""
        user = _make_user()
        mock_db = _build_mock_session()

        cases = [
            _make_case(user.id, domain=CaseDomain.traffic_violation),
            _make_case(user.id, domain=CaseDomain.small_claims),
        ]
        mock_db.execute.side_effect = [_mock_count_result(len(cases)), _mock_scalars_result(cases)]

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/cases/")

        assert resp.status_code == 200
        data = resp.json()
        items = data["items"]
        assert len(items) == 2
        assert items[0]["case_id"]
        assert items[0]["pipeline_progress"]["pipeline_progress_percent"] == 0


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
        assert data["title"] == case.title
        assert "hearing_analyses" in data

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


class TestProcessCase:
    @staticmethod
    def _doc() -> MagicMock:
        doc = MagicMock()
        doc.id = uuid.uuid4()
        doc.filename = "evidence.pdf"
        doc.file_type = "application/pdf"
        return doc

    async def test_process_case_accepted(self):
        """POST /api/v1/cases/{id}/process returns 202 when the atomic flip matches.

        Also asserts that a PipelineJob outbox row was staged on the same
        session as the status flip — the crash-safety contract depends on
        the INSERT sharing the commit, not being dispatched inline.
        """
        user = _make_user()
        mock_db = _build_mock_session()

        case = _make_case(user.id, documents=[self._doc()])
        mock_db.execute.side_effect = [
            _mock_scalar_result(case),
            _mock_scalar_result(case.id),
        ]

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/v1/cases/{case.id}/process")

        assert resp.status_code == 202
        assert resp.json()["message"]
        mock_db.commit.assert_awaited()

        # Outbox contract: one PipelineJob row for this case must be staged.
        from src.models.pipeline_job import PipelineJob, PipelineJobType

        mock_db.add.assert_called_once()
        (staged,) = mock_db.add.call_args.args
        assert isinstance(staged, PipelineJob)
        assert staged.job_type == PipelineJobType.case_pipeline
        assert staged.case_id == case.id
        assert staged.target_id is None

    async def test_process_case_rejects_empty_documents(self):
        """POST /process returns 400 when the case has no documents."""
        user = _make_user()
        mock_db = _build_mock_session()

        case = _make_case(user.id, documents=[])
        mock_db.execute.return_value = _mock_scalar_result(case)

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/v1/cases/{case.id}/process")

        assert resp.status_code == 400

    async def test_process_case_not_found(self):
        """POST /process on an unknown case returns 404."""
        user = _make_user()
        mock_db = _build_mock_session()
        mock_db.execute.return_value = _mock_scalar_result(None)

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/v1/cases/{uuid.uuid4()}/process")

        assert resp.status_code == 404

    async def test_process_case_rejects_non_startable_status(self):
        """Ready-for-review / closed / processing cases return 409 from the atomic flip."""
        user = _make_user()
        mock_db = _build_mock_session()

        case = _make_case(user.id, documents=[self._doc()], status=CaseStatus.ready_for_review)
        mock_db.execute.side_effect = [
            _mock_scalar_result(case),
            _mock_scalar_result(None),
        ]

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/v1/cases/{case.id}/process")

        assert resp.status_code == 409
        mock_db.commit.assert_not_awaited()

    async def test_process_case_rejects_concurrent_start(self):
        """If two POSTs race, one wins the atomic flip; the other gets 409."""
        user = _make_user()
        mock_db = _build_mock_session()

        case = _make_case(user.id, documents=[self._doc()], status=CaseStatus.processing)
        mock_db.execute.side_effect = [
            _mock_scalar_result(case),
            _mock_scalar_result(None),
        ]

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/v1/cases/{case.id}/process")

        assert resp.status_code == 409

    async def test_process_case_accepts_ready_for_review(self):
        """ready_for_review is explicitly startable per STARTABLE_STATUSES."""
        user = _make_user()
        mock_db = _build_mock_session()

        case = _make_case(user.id, documents=[self._doc()], status=CaseStatus.ready_for_review)
        mock_db.execute.side_effect = [
            _mock_scalar_result(case),
            _mock_scalar_result(case.id),
        ]

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/v1/cases/{case.id}/process")

        assert resp.status_code == 202


class TestCors:
    async def test_preflight_allows_vite_origin(self):
        """OPTIONS preflight from the Vite dev origin is permitted."""
        app = create_app()
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.options(
                "/api/v1/cases/",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "Content-Type",
                },
            )

        assert resp.status_code in (200, 204)
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


class TestGetCaseResponseShape:
    async def test_response_contains_all_nested_entities(self):
        """GET /cases/{id} response includes all 12 nested entity lists."""
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

        expected_keys = {
            "id",
            "case_id",
            "title",
            "description",
            "domain",
            "status",
            "status_group",
            "jurisdiction",
            "complexity",
            "route",
            "created_by",
            "pipeline_progress",
            "parties",
            "documents",
            "evidence",
            "facts",
            "witnesses",
            "legal_rules",
            "precedents",
            "arguments",
            "hearing_analyses",
            "audit_logs",
        }
        assert expected_keys.issubset(data.keys()), f"Missing keys: {expected_keys - data.keys()}"

        # All nested lists should be present (even if empty)
        for key in [
            "parties",
            "documents",
            "evidence",
            "facts",
            "witnesses",
            "legal_rules",
            "precedents",
            "arguments",
            "hearing_analyses",
            "audit_logs",
        ]:
            assert isinstance(data[key], list), f"{key} should be a list"

    async def test_datetime_serialization_format(self):
        """Datetime fields serialize to ISO 8601 format."""
        user = _make_user()
        mock_db = _build_mock_session()

        now = datetime.now(UTC)
        doc = MagicMock()
        doc.id = uuid.uuid4()
        doc.filename = "test.pdf"
        doc.file_type = "application/pdf"
        doc.uploaded_at = now

        case = _make_case(user.id, documents=[doc])
        mock_db.execute.return_value = _mock_scalar_result(case)

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/cases/{case.id}")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["documents"]) == 1
        uploaded_at = data["documents"][0]["uploaded_at"]
        assert uploaded_at is not None
        # Verify it parses as ISO 8601
        parsed = datetime.fromisoformat(uploaded_at)
        assert parsed.year == now.year
