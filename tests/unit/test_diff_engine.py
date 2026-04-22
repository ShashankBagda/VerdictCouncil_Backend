"""Unit tests for src.services.whatif_controller.diff_engine.generate_diff."""

from __future__ import annotations

import copy

from src.shared.case_state import CaseDomainEnum, CaseState, CaseStatusEnum

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def _base_case_state() -> CaseState:
    """Return a fully populated CaseState to use as the 'original'.

    Field values match the keys expected by diff_engine internals:
    - evidence_analysis uses "evidence_items" (not "items")
    - extracted_facts uses "status" on each fact (not "disputed")
    - verdict_recommendation uses "recommendation_type", "recommended_outcome", "confidence_score"
    - deliberation uses "preliminary_conclusion" and "confidence_score"
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
            "confidence_score": 80,
        },
        fairness_check={
            "critical_issues_found": False,
            "audit_passed": True,
            "issues": [],
            "recommendations": [],
        },
        verdict_recommendation={
            "recommendation_type": "guilty",
            "recommended_outcome": "Respondent is liable.",
            "confidence_score": 80,
            "reasoning": "Balance of evidence favours claimant.",
            "alternative_outcomes": [],
        },
    )


# ------------------------------------------------------------------ #
# Verdict change detection
# ------------------------------------------------------------------ #


class TestVerdictChange:
    def test_verdict_changed_detected(self):
        """Different verdicts should yield verdict_changed=True."""
        from src.services.whatif_controller.diff_engine import generate_diff

        original = _base_case_state()
        modified = copy.deepcopy(original)
        modified.verdict_recommendation = modified.verdict_recommendation.model_copy(
            update={"recommendation_type": "not_guilty"}
        )

        diff = generate_diff(original, modified)

        assert diff["verdict_changed"] is True

    def test_verdict_unchanged(self):
        """Same verdict should yield verdict_changed=False."""
        from src.services.whatif_controller.diff_engine import generate_diff

        original = _base_case_state()
        modified = copy.deepcopy(original)
        # Verdict stays "guilty"

        diff = generate_diff(original, modified)

        assert diff["verdict_changed"] is False


# ------------------------------------------------------------------ #
# Confidence delta
# ------------------------------------------------------------------ #


class TestConfidenceDelta:
    def test_confidence_delta_calculated(self):
        """Confidence drop from 80 to 65 should produce delta=-15."""
        from src.services.whatif_controller.diff_engine import generate_diff

        original = _base_case_state()
        modified = copy.deepcopy(original)
        modified.verdict_recommendation = modified.verdict_recommendation.model_copy(
            update={"confidence_score": 65}
        )

        diff = generate_diff(original, modified)

        assert diff["confidence_delta"] == -15


# ------------------------------------------------------------------ #
# Fact changes
# ------------------------------------------------------------------ #


class TestFactChanges:
    def test_fact_changes_listed(self):
        """Toggling a fact's status should appear in fact_changes."""
        from src.services.whatif_controller.diff_engine import generate_diff

        original = _base_case_state()
        modified = copy.deepcopy(original)

        # Toggle fact f-2 from "disputed" to "agreed"
        for fact in modified.extracted_facts.facts:
            if fact["id"] == "f-2":
                fact["status"] = "agreed"

        diff = generate_diff(original, modified)

        assert "fact_changes" in diff
        assert len(diff["fact_changes"]) > 0
        # The changed fact should be identifiable
        changed_ids = [c.get("fact_id") for c in diff["fact_changes"]]
        assert "f-2" in changed_ids


# ------------------------------------------------------------------ #
# Evidence changes
# ------------------------------------------------------------------ #


class TestEvidenceChanges:
    def test_evidence_changes_listed(self):
        """Excluding an evidence item should appear in evidence_changes."""
        from src.services.whatif_controller.diff_engine import generate_diff

        original = _base_case_state()
        modified = copy.deepcopy(original)

        # Mark evidence ev-2 as excluded
        for item in modified.evidence_analysis.evidence_items:
            if item["id"] == "ev-2":
                item["excluded"] = True

        diff = generate_diff(original, modified)

        assert "evidence_changes" in diff
        assert len(diff["evidence_changes"]) > 0
        # The excluded evidence should be identifiable
        changed_ids = [c.get("evidence_id") for c in diff["evidence_changes"]]
        assert "ev-2" in changed_ids
