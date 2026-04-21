"""Unit tests for src.services.whatif_controller.controller.WhatIfController."""

from __future__ import annotations

import copy
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.pipeline.runner import AGENT_ORDER
from src.shared.case_state import CaseDomainEnum, CaseState, CaseStatusEnum

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def _populated_case_state() -> CaseState:
    """Return a CaseState with realistic populated fields."""
    return CaseState(
        domain=CaseDomainEnum.small_claims,
        status=CaseStatusEnum.decided,
        parties=[
            {"name": "Alice Tan", "role": "claimant"},
            {"name": "Bob Lee", "role": "respondent"},
        ],
        case_metadata={
            "filed_date": "2026-02-10",
            "category": "small_claims",
            "subcategory": "property_damage",
            "jurisdiction_valid": True,
        },
        raw_documents=[
            {"doc_id": "doc-1", "type": "claim_form", "text": "Claimant alleges damage."},
        ],
        evidence_analysis={
            "evidence_items": [
                {"id": "ev-1", "type": "photo", "weight": 0.8, "description": "Damaged wall"},
                {"id": "ev-2", "type": "receipt", "weight": 0.6, "description": "Repair invoice"},
            ],
        },
        extracted_facts={
            "facts": [
                {"id": "f-1", "text": "Wall was damaged on 2026-01-15", "status": "agreed"},
                {"id": "f-2", "text": "Respondent was present at the time", "status": "disputed"},
            ],
            "timeline": [
                {"date": "2026-01-15", "event": "Damage occurred"},
            ],
        },
        witnesses={
            "witnesses": [
                {
                    "id": "w-1",
                    "name": "Charlie",
                    "credibility_score": 75,
                    "statement": "I saw it happen",
                },
            ],
        },
        legal_rules=[
            {"statute": "Small Claims Act s12", "relevance": "high"},
        ],
        precedents=[
            {"case_name": "Tan v Lee [2024]", "relevance": 0.85},
        ],
        arguments={
            "prosecution": {"overall_strength": 0.8},
            "defense": {"overall_strength": 0.4},
        },
        deliberation={
            "preliminary_conclusion": "Balance of evidence favours claimant.",
            "confidence_score": 80,
        },
        fairness_check={"critical_issues_found": False, "issues": []},
        verdict_recommendation={
            "recommendation_type": "liable",
            "recommended_outcome": "Respondent is liable for property damage.",
            "confidence_score": 80,
        },
    )


def _mock_pipeline_runner():
    """Return a mock PipelineRunner with _run_agent as an AsyncMock."""
    runner = MagicMock()
    runner._run_agent = AsyncMock(side_effect=lambda agent_name, state: state)
    return runner


# ------------------------------------------------------------------ #
# CHANGE_IMPACT_MAP: modification type -> correct start agent
# ------------------------------------------------------------------ #


class TestChangeImpactMapping:
    """Verify each modification type routes to the correct start agent."""

    @pytest.mark.asyncio
    async def test_fact_toggle_starts_at_agent_7(self):
        """fact_toggle should start pipeline at argument_construction (agent 7)."""
        from src.services.whatif_controller.controller import WhatIfController

        runner = _mock_pipeline_runner()
        controller = WhatIfController(runner)
        state = _populated_case_state()

        await controller.create_scenario(
            case_state=state,
            modification_type="fact_toggle",
            modification_payload={"fact_id": "f-2", "new_status": "agreed"},
        )

        # Verify the mapped start agent
        assert WhatIfController.CHANGE_IMPACT_MAP["fact_toggle"] == "argument_construction"

        # Verify _run_agent was called with the correct agent sequence
        called_agents = [call.args[0] for call in runner._run_agent.call_args_list]
        start_index = AGENT_ORDER.index("argument_construction")
        expected_agents = AGENT_ORDER[start_index:]
        assert called_agents == expected_agents

    @pytest.mark.asyncio
    async def test_evidence_exclusion_starts_at_agent_3(self):
        """evidence_exclusion should start pipeline at evidence_analysis (agent 3)."""
        from src.services.whatif_controller.controller import WhatIfController

        runner = _mock_pipeline_runner()
        controller = WhatIfController(runner)
        state = _populated_case_state()

        await controller.create_scenario(
            case_state=state,
            modification_type="evidence_exclusion",
            modification_payload={"evidence_id": "ev-2", "exclude": True},
        )

        assert WhatIfController.CHANGE_IMPACT_MAP["evidence_exclusion"] == "evidence_analysis"

        called_agents = [call.args[0] for call in runner._run_agent.call_args_list]
        start_index = AGENT_ORDER.index("evidence_analysis")
        expected_agents = AGENT_ORDER[start_index:]
        assert called_agents == expected_agents

    @pytest.mark.asyncio
    async def test_legal_interpretation_starts_at_agent_6(self):
        """legal_interpretation should start at legal_knowledge (agent 6).

        This was the bug fixed in Phase -1: it previously routed to
        argument_construction instead of legal_knowledge.
        """
        from src.services.whatif_controller.controller import WhatIfController

        runner = _mock_pipeline_runner()
        controller = WhatIfController(runner)
        state = _populated_case_state()

        await controller.create_scenario(
            case_state=state,
            modification_type="legal_interpretation",
            modification_payload={"rule_index": 0, "new_application": "narrow"},
        )

        assert WhatIfController.CHANGE_IMPACT_MAP["legal_interpretation"] == "legal_knowledge"

        called_agents = [call.args[0] for call in runner._run_agent.call_args_list]
        start_index = AGENT_ORDER.index("legal_knowledge")
        expected_agents = AGENT_ORDER[start_index:]
        assert called_agents == expected_agents

    @pytest.mark.asyncio
    async def test_witness_credibility_starts_at_agent_7(self):
        """witness_credibility should start at argument_construction (agent 7)."""
        from src.services.whatif_controller.controller import WhatIfController

        runner = _mock_pipeline_runner()
        controller = WhatIfController(runner)
        state = _populated_case_state()

        await controller.create_scenario(
            case_state=state,
            modification_type="witness_credibility",
            modification_payload={"witness_id": "w-1", "new_credibility_score": 30},
        )

        assert WhatIfController.CHANGE_IMPACT_MAP["witness_credibility"] == "argument_construction"

        called_agents = [call.args[0] for call in runner._run_agent.call_args_list]
        start_index = AGENT_ORDER.index("argument_construction")
        expected_agents = AGENT_ORDER[start_index:]
        assert called_agents == expected_agents


# ------------------------------------------------------------------ #
# Scenario isolation and identity
# ------------------------------------------------------------------ #


class TestScenarioIsolation:
    """Verify that scenarios are properly isolated from the original state."""

    @pytest.mark.asyncio
    async def test_deep_clone_doesnt_affect_original(self):
        """Creating a scenario must not mutate the original CaseState."""
        from src.services.whatif_controller.controller import WhatIfController

        runner = _mock_pipeline_runner()
        controller = WhatIfController(runner)
        original = _populated_case_state()
        original_dict = original.model_dump()

        await controller.create_scenario(
            case_state=original,
            modification_type="fact_toggle",
            modification_payload={"fact_id": "f-2", "new_status": "agreed"},
        )

        # Original must be identical to its snapshot before the call
        assert original.model_dump() == original_dict

    @pytest.mark.asyncio
    async def test_new_run_id_generated(self):
        """Scenario should get a new run_id; parent_run_id set to the original."""
        from src.services.whatif_controller.controller import WhatIfController

        runner = _mock_pipeline_runner()
        # Capture the state passed to _run_agent to inspect its IDs
        passed_states: list[CaseState] = []

        async def capture_run(agent_name, state):
            passed_states.append(copy.deepcopy(state))
            return state

        runner._run_agent = AsyncMock(side_effect=capture_run)

        controller = WhatIfController(runner)
        original = _populated_case_state()
        original_run_id = original.run_id

        await controller.create_scenario(
            case_state=original,
            modification_type="fact_toggle",
            modification_payload={"fact_id": "f-2", "new_status": "agreed"},
        )

        assert len(passed_states) > 0
        scenario_state = passed_states[0]
        # New run_id must differ from original
        assert scenario_state.run_id != original_run_id
        # parent_run_id must reference the original
        assert scenario_state.parent_run_id == original_run_id


# ------------------------------------------------------------------ #
# Invalid modification type
# ------------------------------------------------------------------ #


class TestInvalidModificationType:
    @pytest.mark.asyncio
    async def test_unknown_modification_type_raises(self):
        """Passing an unknown modification_type should raise ValueError."""
        from src.services.whatif_controller.controller import WhatIfController

        runner = _mock_pipeline_runner()
        controller = WhatIfController(runner)
        state = _populated_case_state()

        with pytest.raises(ValueError, match="Unknown modification_type"):
            await controller.create_scenario(
                case_state=state,
                modification_type="nonexistent_type",
                modification_payload={},
            )
