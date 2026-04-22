"""Unit tests for WhatIfController.compute_stability_score."""

from __future__ import annotations

import asyncio
import copy
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.shared.case_state import CaseDomainEnum, CaseState, CaseStatusEnum

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def _decided_case_state(verdict: str = "liable", confidence: int = 80) -> CaseState:
    """Return a decided CaseState with the given verdict and confidence.

    Uses field keys matching the actual implementation:
    - evidence_analysis -> "evidence_items"
    - extracted_facts -> facts with "status" field
    - verdict_recommendation -> "recommendation_type", "recommended_outcome", "confidence_score"
    - deliberation -> "preliminary_conclusion", "confidence_score"
    """
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
        },
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
        },
        witnesses={
            "witnesses": [
                {"id": "w-1", "name": "Charlie", "credibility_score": 75},
            ],
        },
        legal_rules=[{"statute": "Small Claims Act s12", "relevance": "high"}],
        precedents=[{"case_name": "Tan v Lee [2024]", "relevance": 0.85}],
        arguments={
            "prosecution": {"overall_strength": 0.8},
            "defense": {"overall_strength": 0.4},
        },
        deliberation={
            "preliminary_conclusion": "Balance of evidence favours claimant.",
            "confidence_score": confidence,
        },
        fairness_check={"critical_issues_found": False, "issues": []},
        verdict_recommendation={
            "recommendation_type": verdict,
            "recommended_outcome": f"Verdict: {verdict}",
            "confidence_score": confidence,
        },
    )


def _mock_runner_with_verdicts(verdicts: list[str]):
    """Return a mock MeshPipelineRunner whose run_from returns a state with the next verdict.

    compute_stability_score runs N perturbations in parallel via
    asyncio.gather. Each call to run_from consumes one verdict from the
    list (cycled), letting the test assert how many perturbations held
    vs. flipped against the original.
    """
    runner = MagicMock()
    verdict_idx = [0]
    lock = asyncio.Lock()

    async def mock_run_from(state, start_agent, run_id=None):
        async with lock:
            v = verdicts[verdict_idx[0] % len(verdicts)]
            verdict_idx[0] += 1
        state = copy.deepcopy(state)
        state.verdict_recommendation = {
            "recommendation_type": v,
            "recommended_outcome": f"Verdict: {v}",
            "confidence_score": 75,
        }
        return state

    runner.run_from = AsyncMock(side_effect=mock_run_from)
    return runner


# ------------------------------------------------------------------ #
# Stability score computation
# ------------------------------------------------------------------ #


class TestStabilityScore:
    @pytest.mark.asyncio
    async def test_all_perturbations_hold_stable(self):
        """N=3, all perturbations return same verdict -> score=100, classification='stable'.

        The case state has 2 facts (f-1 agreed, f-2 disputed) and 2 evidence items,
        giving 4 possible perturbations. With n=3, only the first 3 are used.
        """
        from src.services.whatif_controller.controller import WhatIfController

        original_verdict = "liable"
        # All perturbations return the same verdict
        runner = _mock_runner_with_verdicts([original_verdict] * 3)

        controller = WhatIfController(runner)
        state = _decided_case_state(verdict=original_verdict)

        result = await controller.compute_stability_score(state, n=3)

        assert result["score"] == 100
        assert result["classification"] == "stable"
        assert result["perturbation_count"] == 3
        assert result["perturbations_held"] == 3

    @pytest.mark.asyncio
    async def test_some_perturbations_flip_moderate(self):
        """With perturbations where 1 flips -> moderately_sensitive.

        We have 4 perturbable inputs (2 facts + 2 evidence). With n=4:
        - 3 hold (same verdict) + 1 flips = score 75 -> moderately_sensitive
        """
        from src.services.whatif_controller.controller import WhatIfController

        original_verdict = "liable"
        # 3 hold, 1 flips
        verdicts = [original_verdict, original_verdict, "not_liable", original_verdict]
        runner = _mock_runner_with_verdicts(verdicts)

        controller = WhatIfController(runner)
        state = _decided_case_state(verdict=original_verdict)

        result = await controller.compute_stability_score(state, n=4)

        assert result["score"] == 75
        assert result["classification"] == "moderately_sensitive"
        assert result["perturbation_count"] == 4

    @pytest.mark.asyncio
    async def test_most_perturbations_flip_sensitive(self):
        """With perturbations where most flip -> highly_sensitive.

        With n=4 and 3 flipping: score = 25 -> highly_sensitive
        """
        from src.services.whatif_controller.controller import WhatIfController

        original_verdict = "liable"
        # 1 holds, 3 flip
        verdicts = ["not_liable", original_verdict, "not_liable", "not_liable"]
        runner = _mock_runner_with_verdicts(verdicts)

        controller = WhatIfController(runner)
        state = _decided_case_state(verdict=original_verdict)

        result = await controller.compute_stability_score(state, n=4)

        assert result["score"] == 25
        assert result["classification"] == "highly_sensitive"
        assert result["perturbation_count"] == 4
