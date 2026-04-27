"""Q2.3b — runner bridges `Case.intake_extraction` into `CaseState`.

Locks the merge contract (Q-B):
- `intake_extraction` is always populated on `CaseState` when present
  on the Case (regardless of confirm-step state).
- When Case columns are EMPTY and `intake_extraction.fields` has
  values, the fields fill `parties` / `case_metadata` on CaseState.
- When Case columns are NON-EMPTY, they win — the bridge does NOT
  overwrite user-confirmed authority.
"""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import MagicMock

import pytest

from src.api.routes.cases import _build_initial_state_overrides


def _make_case(
    *,
    parties: list | None = None,
    title: str = "Q2.3b case",
    description: str = "Test case for the bridge.",
    filed_date=None,
    claim_amount=None,
    consent_to_higher_claim_limit: bool = False,
    offence_code: str | None = None,
    intake_extraction: dict | None = None,
) -> MagicMock:
    case = MagicMock()
    case.id = uuid.uuid4()
    case.parties = parties or []
    case.title = title
    case.description = description
    case.filed_date = filed_date or date(2026, 4, 1)
    case.claim_amount = claim_amount
    case.consent_to_higher_claim_limit = consent_to_higher_claim_limit
    case.offence_code = offence_code
    case.intake_extraction = intake_extraction
    return case


def _party(name: str, role: str = "claimant", contact_info=None) -> MagicMock:
    p = MagicMock()
    p.name = name
    p.role = MagicMock()
    p.role.value = role
    p.contact_info = contact_info
    return p


def test_intake_extraction_populated_on_state_when_case_has_it():
    """Bridge always carries `case.intake_extraction` onto CaseState
    so downstream agents see what the extractor produced."""
    extraction = {
        "fields": {"parties": [{"name": "Alice", "role": "claimant"}]},
        "citations": [],
    }
    case = _make_case(parties=[], intake_extraction=extraction)

    overrides = _build_initial_state_overrides(case)

    assert overrides["intake_extraction"] == extraction


def test_intake_extraction_none_when_case_has_none():
    """No extraction → CaseState.intake_extraction stays None."""
    case = _make_case(parties=[], intake_extraction=None)

    overrides = _build_initial_state_overrides(case)

    assert overrides["intake_extraction"] is None


def test_empty_case_parties_filled_from_intake_extraction_fields():
    """Q-B / option A: empty Case.parties + populated extraction →
    CaseState.parties is filled from extraction."""
    extraction = {
        "fields": {
            "parties": [
                {"name": "Alice", "role": "claimant"},
                {"name": "Bob", "role": "respondent"},
            ]
        }
    }
    case = _make_case(parties=[], intake_extraction=extraction)

    overrides = _build_initial_state_overrides(case)

    assert overrides["parties"] == [
        {"name": "Alice", "role": "claimant", "contact_info": None},
        {"name": "Bob", "role": "respondent", "contact_info": None},
    ]


def test_non_empty_case_parties_win_over_intake_extraction():
    """Q-B: judge-confirmed authority. Case.parties non-empty →
    CaseState.parties = Case columns; extraction does NOT overwrite."""
    extraction = {
        "fields": {
            "parties": [{"name": "ExtractionAlice", "role": "claimant"}]
        }
    }
    case = _make_case(
        parties=[_party("ConfirmedAlice", "claimant")],
        intake_extraction=extraction,
    )

    overrides = _build_initial_state_overrides(case)

    assert overrides["parties"] == [
        {"name": "ConfirmedAlice", "role": "claimant", "contact_info": None}
    ]


def test_empty_case_metadata_columns_filled_from_extraction():
    """Empty offence_code on Case + populated in extraction.fields →
    CaseState.case_metadata.offence_code carries the extracted value."""
    extraction = {
        "fields": {
            "offence_code": "RTA-S64",
            "claim_amount": "5000.00",
        }
    }
    case = _make_case(
        parties=[_party("Alice")],  # parties non-empty so we test metadata path
        offence_code=None,
        claim_amount=None,
        intake_extraction=extraction,
    )

    overrides = _build_initial_state_overrides(case)

    assert overrides["case_metadata"]["offence_code"] == "RTA-S64"
    assert overrides["case_metadata"]["claim_amount"] == "5000.00"


def test_non_empty_case_metadata_columns_win_over_extraction():
    """Confirmed offence_code on Case → extraction does NOT overwrite."""
    extraction = {"fields": {"offence_code": "EXTRACTED-CODE"}}
    case = _make_case(
        parties=[_party("Alice")],
        offence_code="CONFIRMED-CODE",
        intake_extraction=extraction,
    )

    overrides = _build_initial_state_overrides(case)

    assert overrides["case_metadata"]["offence_code"] == "CONFIRMED-CODE"


def test_extraction_with_empty_fields_does_not_break_anything():
    """Defensive: extraction present but fields={} → behave as if no
    extraction. No crash, no spurious overrides."""
    case = _make_case(
        parties=[_party("Alice")],
        intake_extraction={"fields": {}, "citations": []},
    )

    overrides = _build_initial_state_overrides(case)

    assert overrides["intake_extraction"] == {"fields": {}, "citations": []}
    assert overrides["parties"] == [
        {"name": "Alice", "role": "claimant", "contact_info": None}
    ]


def test_intake_extraction_not_a_dict_treated_as_absent():
    """Defensive: if Case.intake_extraction is somehow not a dict
    (corruption, mocking surprise), treat as absent — don't crash."""
    case = _make_case(parties=[_party("Alice")], intake_extraction="garbage")

    overrides = _build_initial_state_overrides(case)

    assert overrides["intake_extraction"] is None


@pytest.mark.parametrize(
    "extraction_field,case_attr,extraction_value,case_value,expected",
    [
        ("title", "title", "extracted-title", None, "extracted-title"),
        ("title", "title", "extracted-title", "confirmed-title", "confirmed-title"),
        ("description", "description", "ext-desc", None, "ext-desc"),
        ("description", "description", "ext-desc", "conf-desc", "conf-desc"),
    ],
)
def test_metadata_field_merge_matrix(
    extraction_field, case_attr, extraction_value, case_value, expected
):
    case = _make_case(
        parties=[_party("Alice")],
        intake_extraction={"fields": {extraction_field: extraction_value}},
        **{case_attr: case_value},
    )

    overrides = _build_initial_state_overrides(case)

    assert overrides["case_metadata"][case_attr] == expected
