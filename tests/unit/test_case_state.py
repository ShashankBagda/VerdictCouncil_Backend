from src.shared.case_state import CaseDomainEnum, CaseState, CaseStatusEnum


class TestCaseState:
    def test_create_valid_state(self, sample_case_state):
        assert sample_case_state.status == CaseStatusEnum.pending
        assert sample_case_state.domain == CaseDomainEnum.traffic_violation
        assert len(sample_case_state.parties) == 2
        assert sample_case_state.audit_log == []

    def test_default_ids_generated(self):
        state = CaseState()
        assert state.case_id
        assert state.run_id
        assert state.parent_run_id is None

    def test_json_round_trip(self, sample_case_state):
        json_str = sample_case_state.model_dump_json()
        restored = CaseState.model_validate_json(json_str)
        assert restored.case_id == sample_case_state.case_id
        assert restored.domain == sample_case_state.domain
        assert restored.parties == sample_case_state.parties

    def test_invalid_status_rejected(self):
        import pytest

        with pytest.raises(ValueError):
            CaseState(status="invalid_status")

    def test_invalid_domain_rejected(self):
        import pytest

        with pytest.raises(ValueError):
            CaseState(domain="invalid_domain")

    def test_optional_fields_default_none(self):
        state = CaseState()
        assert state.evidence_analysis is None
        assert state.extracted_facts is None
        assert state.witnesses is None
        assert state.hearing_analysis is None
        assert state.fairness_check is None

    def test_intake_extraction_defaults_to_none(self):
        """Q2.3a: the new field defaults to None so v2 callers that don't
        pass it round-trip unchanged."""
        state = CaseState()
        assert state.intake_extraction is None

    def test_intake_extraction_round_trips(self):
        """Q2.3a: a populated intake_extraction survives serialise/load."""
        state = CaseState(
            intake_extraction={
                "fields": {"parties": [{"name": "Alice"}]},
                "citations": [],
            }
        )
        restored = CaseState.model_validate_json(state.model_dump_json())
        assert restored.intake_extraction == {
            "fields": {"parties": [{"name": "Alice"}]},
            "citations": [],
        }
