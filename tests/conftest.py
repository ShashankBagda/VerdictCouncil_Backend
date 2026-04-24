import pytest


@pytest.fixture
def sample_case_state():
    """Provide a minimal valid CaseState for testing."""
    from src.shared.case_state import CaseDomainEnum, CaseState

    return CaseState(
        domain=CaseDomainEnum.traffic_violation,
        parties=[
            {"name": "Prosecution", "role": "prosecution"},
            {"name": "John Doe", "role": "accused"},
        ],
        case_metadata={
            "filed_date": "2026-03-15",
            "category": "traffic",
            "subcategory": "speeding",
            "offence_code": "RTA-S64",
            "jurisdiction_valid": True,
            "jurisdiction_issues": [],
        },
    )
