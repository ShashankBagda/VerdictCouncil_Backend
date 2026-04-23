"""Unit tests for the hearing pack zip export endpoint (US-020)."""

import io
import json
import uuid
import zipfile
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.deps import get_current_user, get_db
from src.models.case import Case
from src.models.user import User, UserRole
from src.services.case_report_data import CaseReportData
from src.services.hearing_pack import _FILES, assemble_pack

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


def _make_case_row(case_id: uuid.UUID, created_by: uuid.UUID) -> MagicMock:
    case = MagicMock(spec=Case)
    case.id = case_id
    case.created_by = created_by
    return case


def _build_mock_session(case: MagicMock | None) -> AsyncMock:
    """Mock session whose execute() always returns a row with our case (or None)."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = case
    session.execute = AsyncMock(return_value=result)
    return session


def _app_with_overrides(mock_db, mock_user):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_current_user] = lambda: mock_user
    return app


def _sample_data(case_id: uuid.UUID | None = None) -> CaseReportData:
    cid = case_id or uuid.uuid4()
    return CaseReportData(
        case_id=cid,
        domain="small_claims",
        status="ready_for_review",
        description="Disputed deposit refund",
        created_at=datetime.now(UTC),
        parties=[{"id": str(uuid.uuid4()), "name": "Alice", "role": "claimant"}],
        evidence=[{"id": str(uuid.uuid4()), "evidence_type": "documentary", "strength": "strong"}],
        facts=[
            {
                "id": str(uuid.uuid4()),
                "description": "Deposit was paid on 2026-01-01",
                "event_date": "2026-01-01",
                "confidence": "high",
                "status": "agreed",
            }
        ],
        arguments=[
            {
                "id": str(uuid.uuid4()),
                "side": "claimant",
                "legal_basis": "Breach of contract",
                "weaknesses": "Receipt is partially illegible",
            }
        ],
        fairness_report={"critical_issues_found": False},
    )


# ---------------------------------------------------------------------------
# Pure assemble_pack tests (no FastAPI)
# ---------------------------------------------------------------------------


class TestAssemblePack:
    def test_pack_contains_all_expected_files(self):
        data = _sample_data()
        pack_bytes = assemble_pack(data)
        with zipfile.ZipFile(io.BytesIO(pack_bytes), "r") as zf:
            names = set(zf.namelist())
        assert names == set(_FILES)

    def test_pack_zip_is_not_corrupt(self):
        data = _sample_data()
        pack_bytes = assemble_pack(data)
        with zipfile.ZipFile(io.BytesIO(pack_bytes), "r") as zf:
            # testzip returns the name of the first bad file, or None if OK
            assert zf.testzip() is None

    def test_manifest_records_correct_counts(self):
        data = _sample_data()
        pack_bytes = assemble_pack(data)
        with zipfile.ZipFile(io.BytesIO(pack_bytes), "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))
        assert manifest["counts"] == {
            "parties": 1,
            "evidence": 1,
            "facts": 1,
            "arguments": 1,
        }
        assert manifest["domain"] == "small_claims"

    def test_evidence_and_facts_are_valid_json(self):
        data = _sample_data()
        pack_bytes = assemble_pack(data)
        with zipfile.ZipFile(io.BytesIO(pack_bytes), "r") as zf:
            evidence = json.loads(zf.read("evidence.json"))
            facts = json.loads(zf.read("facts.json"))
            fairness_governance = json.loads(zf.read("fairness_governance.json"))
        assert evidence[0]["evidence_type"] == "documentary"
        assert facts[0]["confidence"] == "high"
        assert fairness_governance["fairness_report"]["critical_issues_found"] is False


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


class TestHearingPackEndpoint:
    async def test_returns_zip_for_owner(self, monkeypatch):
        user = _make_user()
        case_id = uuid.uuid4()
        case_row = _make_case_row(case_id, user.id)
        mock_db = _build_mock_session(case_row)

        async def _fake_build(db, cid):
            assert cid == case_id
            return _sample_data(case_id)

        from src.api.routes import cases as cases_module

        monkeypatch.setattr(cases_module, "build_case_report_data", _fake_build)

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/cases/{case_id}/hearing-pack")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert (
            resp.headers["content-disposition"]
            == f'attachment; filename="case-{case_id}-hearing-pack.zip"'
        )
        with zipfile.ZipFile(io.BytesIO(resp.content), "r") as zf:
            assert "manifest.json" in zf.namelist()

    async def test_returns_404_when_case_missing(self, monkeypatch):
        user = _make_user()
        mock_db = _build_mock_session(case=None)

        from src.api.routes import cases as cases_module

        async def _fake_build(db, cid):
            return None

        monkeypatch.setattr(cases_module, "build_case_report_data", _fake_build)

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/cases/{uuid.uuid4()}/hearing-pack")

        assert resp.status_code == 404

    async def test_clerk_cannot_export_other_users_case(self, monkeypatch):
        owner = _make_user(role=UserRole.clerk, email="owner@example.com")
        intruder = _make_user(role=UserRole.clerk, email="intruder@example.com")
        case_id = uuid.uuid4()
        case_row = _make_case_row(case_id, owner.id)
        mock_db = _build_mock_session(case_row)

        from src.api.routes import cases as cases_module

        async def _fake_build(db, cid):
            return _sample_data(case_id)

        monkeypatch.setattr(cases_module, "build_case_report_data", _fake_build)

        app = _app_with_overrides(mock_db, intruder)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/cases/{case_id}/hearing-pack")

        assert resp.status_code == 403
