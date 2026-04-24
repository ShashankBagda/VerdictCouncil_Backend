"""Pipeline evaluation runner.

Runs gold-set fixtures through the full pipeline and scores outputs.
Requires OpenAI API access — run with: pytest tests/eval/ -m eval

Usage:
    pytest tests/eval/eval_runner.py -m eval -v
"""

import pytest
from src.pipeline.mesh_runner_factory import get_mesh_runner

from src.shared.case_state import CaseState

from .fixtures import ALL_FIXTURES


def _score_output(state: CaseState) -> dict:
    """Score a pipeline output for completeness and quality."""
    scores = {}

    # Completeness: check that key fields are populated
    expected_fields = [
        "evidence_analysis",
        "extracted_facts",
        "witnesses",
        "legal_rules",
        "precedents",
        "arguments",
        "hearing_analysis",
        "fairness_check",
    ]
    populated = sum(1 for f in expected_fields if getattr(state, f, None))
    scores["completeness"] = populated / len(expected_fields)
    scores["populated_fields"] = populated
    scores["total_fields"] = len(expected_fields)

    # Hearing analysis quality
    hearing_analysis = state.hearing_analysis
    if hearing_analysis is not None:
        scores["has_hearing_analysis"] = True
    else:
        scores["has_hearing_analysis"] = False

    # Fairness check
    fairness = state.fairness_check
    if fairness is not None:
        scores["has_fairness"] = True
        scores["audit_passed_present"] = True
    else:
        scores["has_fairness"] = False
        scores["audit_passed_present"] = False

    # Overall pass: completeness >= 70%, has hearing analysis, has fairness
    scores["passed"] = scores["completeness"] >= 0.7 and scores["has_hearing_analysis"] and scores["has_fairness"]

    return scores


@pytest.mark.eval
class TestPipelineEval:
    """End-to-end pipeline evaluation against gold-set fixtures.

    These tests make real OpenAI API calls and are slow/expensive.
    Only run explicitly: pytest tests/eval/ -m eval
    """

    @pytest.fixture
    async def runner(self):
        return await get_mesh_runner()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "fixture",
        ALL_FIXTURES,
        ids=[f["case_id"] for f in ALL_FIXTURES],
    )
    async def test_pipeline_produces_valid_output(self, runner, fixture):
        """Run a fixture through the pipeline and verify output quality."""
        state = CaseState(**fixture)
        result = await runner.run(state)

        scores = _score_output(result)

        # Print score report
        print(f"\n--- Eval: {fixture['case_id']} ---")
        print(f"  Completeness: {scores['completeness']:.0%} ({scores['populated_fields']}/{scores['total_fields']})")
        print(f"  Has hearing analysis: {scores['has_hearing_analysis']}")
        print(f"  Has fairness: {scores['has_fairness']}")
        print(f"  Audit passed present: {scores['audit_passed_present']}")
        print(f"  PASSED: {scores['passed']}")

        assert scores["completeness"] >= 0.7, f"Completeness too low: {scores['completeness']:.0%}"
        assert scores["has_hearing_analysis"], "No hearing_analysis produced"
        assert scores["has_fairness"], "No fairness_check produced"
