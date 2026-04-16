"""Unit tests for the case report PDF export endpoint (US-027)."""

import io
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pypdf
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.deps import get_current_user, get_db
from src.models.case import Case
from src.models.user import User, UserRole
from src.services.case_report_data import CaseReportData
from src.services.pdf_export import render_case_report_pdf

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
        evidence=[
            {
                "id": str(uuid.uuid4()),
                "evidence_type": "documentary",
                "strength": "strong",
                "admissibility_flags": None,
                "linked_claims": None,
            }
        ],
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
        verdict={
            "id": str(uuid.uuid4()),
            "recommendation_type": "compensation",
            "recommended_outcome": "Refund deposit in full",
            "confidence_score": 87,
            "sentence": None,
            "alternative_outcomes": None,
            "created_at": datetime.now(UTC).isoformat(),
        },
        fairness_report={"critical_issues_found": False},
    )


# ---------------------------------------------------------------------------
# Pure rendering tests (require WeasyPrint + system pango libs)
# ---------------------------------------------------------------------------


def _weasyprint_runtime_available() -> bool:
    """Return True iff the WeasyPrint runtime can render PDFs in this env.

    On macOS without ``brew install pango`` (the default local dev state) the
    import succeeds but ``HTML.write_pdf()`` raises ``OSError`` because pango
    cannot be loaded. CI's Linux image has the system deps installed, so the
    real assertions run there.
    """
    try:
        from weasyprint import HTML  # noqa: F401

        HTML(string="<p>probe</p>").write_pdf()
    except Exception:
        return False
    return True


@pytest.mark.skipif(
    not _weasyprint_runtime_available(),
    reason="WeasyPrint system dependencies (pango) not installed in this environment",
)
class TestRenderCaseReportPdf:
    def test_returns_bytes_with_pdf_magic(self):
        data = _sample_data()
        pdf_bytes = render_case_report_pdf(data)
        assert isinstance(pdf_bytes, bytes)
        assert pdf_bytes.startswith(b"%PDF-")

    def test_pdf_has_at_least_one_page(self):
        data = _sample_data()
        pdf_bytes = render_case_report_pdf(data)
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        assert len(reader.pages) >= 1


# ---------------------------------------------------------------------------
# Endpoint tests (rendering is patched so they do NOT need WeasyPrint runtime)
# ---------------------------------------------------------------------------


class TestCaseReportPdfEndpoint:
    async def test_returns_pdf_for_owner(self, monkeypatch):
        user = _make_user()
        case_id = uuid.uuid4()
        case_row = _make_case_row(case_id, user.id)
        mock_db = _build_mock_session(case_row)

        from src.api.routes import cases as cases_module

        async def _fake_build(db, cid):
            return _sample_data(case_id)

        # Patch render so this test does not depend on WeasyPrint system deps.
        # The pure-rendering tests above exercise the real renderer when available.
        monkeypatch.setattr(cases_module, "build_case_report_data", _fake_build)
        monkeypatch.setattr(
            cases_module,
            "render_case_report_pdf",
            lambda data: b"%PDF-1.4\n%fake test pdf body\n%%EOF",
        )

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/cases/{case_id}/report.pdf")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert (
            resp.headers["content-disposition"]
            == f'attachment; filename="case-{case_id}-report.pdf"'
        )
        assert resp.content.startswith(b"%PDF-")

    async def test_returns_404_when_case_missing(self, monkeypatch):
        user = _make_user()
        mock_db = _build_mock_session(case=None)

        from src.api.routes import cases as cases_module

        async def _fake_build(db, cid):
            return None

        monkeypatch.setattr(cases_module, "build_case_report_data", _fake_build)
        monkeypatch.setattr(cases_module, "render_case_report_pdf", lambda data: b"")

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/cases/{uuid.uuid4()}/report.pdf")

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
        monkeypatch.setattr(cases_module, "render_case_report_pdf", lambda data: b"")

        app = _app_with_overrides(mock_db, intruder)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/cases/{case_id}/report.pdf")

        assert resp.status_code == 403
