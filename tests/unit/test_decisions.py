"""Unit tests for judge decision endpoints (POST /cases/{id}/decision)."""

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


def _make_case(
    created_by: uuid.UUID,
    status: CaseStatus = CaseStatus.ready_for_review,
    **overrides: object,
) -> MagicMock:
    defaults = {
        "id": uuid.uuid4(),
        "domain": CaseDomain.traffic_violation,
        "status": status,
        "jurisdiction_valid": True,
        "complexity": None,
        "route": None,
        "created_by": created_by,
        "created_at": datetime.now(UTC),
        "updated_at": None,
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


DECISION_URL = "/api/v1/cases/{case_id}/decision"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAcceptVerdict:
    async def test_accept_verdict(self):
        """POST decision with action=accept transitions case to decided."""
        judge = _make_user()
        case = _make_case(judge.id, status=CaseStatus.ready_for_review)

        mock_db = _build_mock_session()
        mock_db.execute.return_value = _mock_scalar_result(case)

        app = _app_with_overrides(mock_db, judge)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                DECISION_URL.format(case_id=case.id),
                json={"action": "accept", "remarks": "Verdict is sound."},
            )

        assert resp.status_code in (200, 201)
        data = resp.json()
        # After acceptance the case status should become "decided"
        assert data.get("status") == "decided" or case.status == CaseStatus.decided


class TestModifyVerdict:
    async def test_modify_verdict(self):
        """POST decision with action=modify transitions case to decided."""
        judge = _make_user()
        case = _make_case(judge.id, status=CaseStatus.ready_for_review)

        mock_db = _build_mock_session()
        mock_db.execute.return_value = _mock_scalar_result(case)

        app = _app_with_overrides(mock_db, judge)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                DECISION_URL.format(case_id=case.id),
                json={
                    "action": "modify",
                    "remarks": "Adjusting sentence.",
                    "modified_outcome": "Reduced fine to $500",
                },
            )

        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data.get("status") == "decided" or case.status == CaseStatus.decided


class TestRejectVerdict:
    async def test_reject_verdict(self):
        """POST decision with action=reject transitions case to decided."""
        judge = _make_user()
        case = _make_case(judge.id, status=CaseStatus.ready_for_review)

        mock_db = _build_mock_session()
        mock_db.execute.return_value = _mock_scalar_result(case)

        app = _app_with_overrides(mock_db, judge)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                DECISION_URL.format(case_id=case.id),
                json={
                    "action": "reject",
                    "remarks": "Insufficient evidence.",
                },
            )

        assert resp.status_code in (200, 201)
        data = resp.json()
        # Rejected verdicts can result in "decided" or "rejected" status
        assert data.get("status") in ("decided", "rejected") or case.status in (
            CaseStatus.decided,
            CaseStatus.rejected,
        )


class TestDecisionWrongStatus:
    async def test_decision_wrong_status(self):
        """POST decision on a case not in ready_for_review returns 400."""
        judge = _make_user()
        # Case is still pending -- not ready for a decision
        case = _make_case(judge.id, status=CaseStatus.pending)

        mock_db = _build_mock_session()
        mock_db.execute.return_value = _mock_scalar_result(case)

        app = _app_with_overrides(mock_db, judge)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                DECISION_URL.format(case_id=case.id),
                json={"action": "accept", "remarks": "Trying on wrong status."},
            )

        assert resp.status_code == 400


class TestDecisionAmendments:
    async def test_recording_judge_can_apply_amendment_directly(self):
        judge = _make_user()
        prior_decision_log = MagicMock()
        prior_decision_log.agent_name = "judge"
        prior_decision_log.action = "decision_modify"
        prior_decision_log.input_payload = {
            "judge_id": str(judge.id),
            "final_order": "Original order",
        }
        prior_decision_log.created_at = datetime.now(UTC)
        verdict = MagicMock()
        verdict.id = uuid.uuid4()
        verdict.created_at = datetime.now(UTC)
        verdict.recommendation_type = "manual_decision"
        verdict.recommended_outcome = "Original order"
        verdict.sentence = None
        verdict.confidence_score = None
        verdict.alternative_outcomes = None
        verdict.fairness_report = None
        case = _make_case(
            judge.id,
            status=CaseStatus.decided,
            audit_logs=[prior_decision_log],
            verdicts=[verdict],
        )

        mock_db = _build_mock_session()
        mock_db.execute.return_value = _mock_scalar_result(case)

        app = _app_with_overrides(mock_db, judge)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/cases/{case.id}/decision-amendments",
                json={
                    "amendment_type": "clerical_correction",
                    "reason": "Correcting the damages amount.",
                    "final_order": "Damages amended to $3,800.",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    async def test_non_recording_judge_routes_amendment_to_senior_inbox(self):
        recording_judge = _make_user()
        requesting_judge = _make_user(email="other@example.com")
        prior_decision_log = MagicMock()
        prior_decision_log.agent_name = "judge"
        prior_decision_log.action = "decision_modify"
        prior_decision_log.input_payload = {
            "judge_id": str(recording_judge.id),
            "final_order": "Original order",
        }
        prior_decision_log.created_at = datetime.now(UTC)
        case = _make_case(
            recording_judge.id,
            status=CaseStatus.decided,
            audit_logs=[prior_decision_log],
            verdicts=[],
        )

        mock_db = _build_mock_session()
        mock_db.execute.return_value = _mock_scalar_result(case)

        app = _app_with_overrides(mock_db, requesting_judge)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/cases/{case.id}/decision-amendments",
                json={
                    "amendment_type": "post_hearing_update",
                    "reason": "Late document clarifies the quantum calculation.",
                    "final_order": "Updated award to $4,100.",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "pending_senior_review"
