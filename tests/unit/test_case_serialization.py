"""Unit tests for case serialization — domain_id, domain_detail, and legacy domain field.

Verifies that _serialize_case_summary and _serialize_case_detail include:
- legacy `domain` enum string (back-compat for the parallel-run window)
- new `domain_id` UUID FK
- new `domain_detail` nested object {id, code, name} when domain_ref is loaded
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

from src.api.routes.cases import _serialize_case_summary
from src.models.case import CaseDomain, CaseStatus


def _make_jurisdiction() -> MagicMock:
    j = MagicMock()
    j.country = "Singapore"
    j.jurisdiction_type = "civil"
    j.court_level = "tribunal"
    j.geographic_region = None
    return j


def _make_case(
    domain: CaseDomain = CaseDomain.small_claims,
    domain_id: uuid.UUID | None = None,
    domain_ref=None,
) -> MagicMock:
    case = MagicMock()
    case.id = uuid.uuid4()
    case.title = "Test Case"
    case.description = "A test case description"
    case.domain = domain
    case.domain_id = domain_id
    case.domain_ref = domain_ref
    case.status = CaseStatus.processing
    case.complexity = None
    case.route = None
    case.created_by = uuid.uuid4()
    case.created_at = datetime.now(UTC)
    case.updated_at = datetime.now(UTC)
    case.filed_date = date.today()
    case.claim_amount = 1500.0
    case.consent_to_higher_claim_limit = False
    case.offence_code = None
    case.parties = []
    case.documents = []
    case.reopen_requests = []
    case.jurisdiction = _make_jurisdiction()
    # Pipeline state
    case.gate_statuses = {}
    case.pipeline_stages = {}
    case.outcome_type = None
    case.outcome_summary = None
    case.current_agent = None
    case.escalation_reason = None
    case.flagged_issues = []
    return case


def _make_domain_ref(
    domain_id: uuid.UUID | None = None,
    code: str = "small_claims",
    name: str = "Small Claims Tribunal",
) -> MagicMock:
    ref = MagicMock()
    ref.id = domain_id or uuid.uuid4()
    ref.code = code
    ref.name = name
    return ref


# ---------------------------------------------------------------------------
# Legacy domain field preserved (back-compat)
# ---------------------------------------------------------------------------


def test_serialize_case_includes_legacy_domain_enum():
    """Legacy 'domain' enum string must appear in the serialized output."""
    case = _make_case(domain=CaseDomain.small_claims)
    result = _serialize_case_summary(case)
    assert "domain" in result
    assert result["domain"] == CaseDomain.small_claims


def test_serialize_case_traffic_domain_enum():
    """traffic_violation domain is serialized correctly."""
    case = _make_case(domain=CaseDomain.traffic_violation)
    result = _serialize_case_summary(case)
    assert result["domain"] == CaseDomain.traffic_violation


# ---------------------------------------------------------------------------
# New domain_id FK field
# ---------------------------------------------------------------------------


def test_serialize_case_includes_domain_id():
    """domain_id UUID FK must appear in the serialized output."""
    did = uuid.uuid4()
    case = _make_case(domain_id=did)
    result = _serialize_case_summary(case)
    assert "domain_id" in result
    assert result["domain_id"] == did


def test_serialize_case_domain_id_none_when_not_set():
    """domain_id is None when no domain FK is set (legacy case)."""
    case = _make_case(domain_id=None)
    result = _serialize_case_summary(case)
    assert result["domain_id"] is None


# ---------------------------------------------------------------------------
# domain_detail nested object
# ---------------------------------------------------------------------------


def test_serialize_case_includes_domain_detail_when_domain_ref_loaded():
    """domain_detail nested {id, code, name} object is included when domain_ref is present."""
    did = uuid.uuid4()
    ref = _make_domain_ref(domain_id=did, code="small_claims", name="Small Claims Tribunal")
    case = _make_case(domain_id=did, domain_ref=ref)

    result = _serialize_case_summary(case)

    assert "domain_detail" in result
    assert result["domain_detail"] is not None
    detail = result["domain_detail"]
    assert detail["id"] == did
    assert detail["code"] == "small_claims"
    assert detail["name"] == "Small Claims Tribunal"


def test_serialize_case_domain_detail_none_when_no_domain_ref():
    """domain_detail is None when domain_ref is not loaded (lazy-load not triggered)."""
    case = _make_case(domain_id=uuid.uuid4(), domain_ref=None)
    result = _serialize_case_summary(case)
    assert result["domain_detail"] is None


def test_serialize_case_both_legacy_and_new_fields_coexist():
    """Both the legacy domain enum and the new domain_id + domain_detail are present together."""
    did = uuid.uuid4()
    ref = _make_domain_ref(domain_id=did, code="traffic_violation", name="Traffic Court")
    case = _make_case(
        domain=CaseDomain.traffic_violation,
        domain_id=did,
        domain_ref=ref,
    )

    result = _serialize_case_summary(case)

    # All three must be present in the same response
    assert result["domain"] == CaseDomain.traffic_violation
    assert result["domain_id"] == did
    assert result["domain_detail"]["code"] == "traffic_violation"
