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
        "deliberation",
        "fairness_check",
        "verdict_recommendation",
    ]
    populated = sum(1 for f in expected_fields if getattr(state, f, None))
    scores["completeness"] = populated / len(expected_fields)
    scores["populated_fields"] = populated
    scores["total_fields"] = len(expected_fields)

    # Verdict quality
    verdict = state.verdict_recommendation
    if isinstance(verdict, dict):
        confidence = verdict.get("confidence_score")
        scores["has_verdict"] = True
        scores["confidence_score"] = confidence
        scores["confidence_valid"] = isinstance(confidence, (int, float)) and 0 <= confidence <= 100
    else:
        scores["has_verdict"] = False
        scores["confidence_valid"] = False

    # Fairness check
    fairness = state.fairness_check
    if isinstance(fairness, dict):
        scores["has_fairness"] = True
        scores["audit_passed_present"] = "audit_passed" in fairness
    else:
        scores["has_fairness"] = False
        scores["audit_passed_present"] = False

    # Overall pass: completeness >= 70%, has verdict, has fairness
    scores["passed"] = (
        scores["completeness"] >= 0.7 and scores["has_verdict"] and scores["has_fairness"]
    )

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
        print(
            f"  Completeness: {scores['completeness']:.0%} "
            f"({scores['populated_fields']}/{scores['total_fields']})"
        )
        print(f"  Has verdict: {scores['has_verdict']}")
        print(f"  Confidence valid: {scores['confidence_valid']}")
        print(f"  Has fairness: {scores['has_fairness']}")
        print(f"  Audit passed present: {scores['audit_passed_present']}")
        print(f"  PASSED: {scores['passed']}")

        assert scores["completeness"] >= 0.7, f"Completeness too low: {scores['completeness']:.0%}"
        assert scores["has_verdict"], "No verdict_recommendation produced"
        assert scores["has_fairness"], "No fairness_check produced"
