import pytest

from src.shared.case_state import CaseState
from src.shared.validation import FieldOwnershipError, validate_field_ownership


class TestFieldOwnership:
    def test_authorized_write_passes(self):
        original = CaseState().model_dump()
        updated = CaseState(domain="small_claims").model_dump()
        # case-processing is allowed to write domain
        validate_field_ownership("case-processing", original, updated)

    def test_unauthorized_write_raises(self):
        original = CaseState().model_dump()
        updated = CaseState(
            hearing_analysis={"preliminary_conclusion": "test", "confidence_score": 80}
        ).model_dump()
        with pytest.raises(FieldOwnershipError, match="case-processing"):
            validate_field_ownership("case-processing", original, updated)

    def test_audit_log_always_allowed(self):
        original = CaseState().model_dump()
        updated = CaseState()
        from src.shared.audit import append_audit_entry

        updated = append_audit_entry(updated, agent="case-processing", action="test")
        validate_field_ownership("case-processing", original, updated.model_dump())

    def test_unknown_agent_cannot_write(self):
        original = CaseState().model_dump()
        updated = CaseState(domain="small_claims").model_dump()
        with pytest.raises(FieldOwnershipError):
            validate_field_ownership("unknown-agent", original, updated)
