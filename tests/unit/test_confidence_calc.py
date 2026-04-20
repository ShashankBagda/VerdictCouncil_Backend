"""Unit tests for src.tools.confidence_calc (pure logic, no mocking)."""

from src.tools.confidence_calc import confidence_calc


# ------------------------------------------------------------------ #
# All strong evidence -> High confidence (80-100)
# ------------------------------------------------------------------ #
def test_all_strong_evidence_high_confidence():
    result = confidence_calc(
        evidence_strengths=["strong", "strong", "strong"],
        fact_statuses=["verified", "verified", "corroborated"],
        witness_scores=[90, 95, 88],
        precedent_similarities=[0.92, 0.88, 0.95],
    )

    assert result["confidence_score"] >= 80
    assert result["classification"] == "High"


# ------------------------------------------------------------------ #
# Mixed evidence -> Medium confidence (60-79)
# ------------------------------------------------------------------ #
def test_mixed_evidence_medium_confidence():
    result = confidence_calc(
        evidence_strengths=["strong", "weak", "moderate"],
        fact_statuses=["verified", "disputed", "corroborated"],
        witness_scores=[75, 50, 80],
        precedent_similarities=[0.70, 0.55, 0.60],
    )

    assert 60 <= result["confidence_score"] <= 79
    assert result["classification"] == "Medium"


# ------------------------------------------------------------------ #
# No/weak evidence -> Low confidence (0-59)
# ------------------------------------------------------------------ #
def test_weak_evidence_low_confidence():
    result = confidence_calc(
        evidence_strengths=["weak", "insufficient"],
        fact_statuses=["unverified", "contradicted"],
        witness_scores=[20, 15],
        precedent_similarities=[0.10, 0.05],
    )

    assert result["confidence_score"] <= 59
    assert result["classification"] == "Low"


def test_empty_inputs_low_confidence():
    result = confidence_calc(
        evidence_strengths=[],
        fact_statuses=[],
        witness_scores=[],
        precedent_similarities=[],
    )

    assert result["confidence_score"] == 0
    assert result["classification"] == "Low"


# ------------------------------------------------------------------ #
# Correct breakdown weights (evidence 30%, facts 25%, witnesses 20%, precedents 25%)
# ------------------------------------------------------------------ #
def test_breakdown_weights_applied_correctly():
    """Each component is set to 100 -> weighted sum should be 100."""
    result = confidence_calc(
        evidence_strengths=["strong"],  # maps to 90
        fact_statuses=["verified"],  # maps to 95
        witness_scores=[100],
        precedent_similarities=[1.0],  # scales to 100
    )

    breakdown = result["breakdown"]
    assert breakdown["evidence"] == 90.0
    assert breakdown["facts"] == 95.0
    assert breakdown["witnesses"] == 100.0
    assert breakdown["precedents"] == 100.0

    # Weighted: 90*0.30 + 95*0.25 + 100*0.20 + 100*0.25 = 27 + 23.75 + 20 + 25 = 95.75
    expected = round(90 * 0.30 + 95 * 0.25 + 100 * 0.20 + 100 * 0.25)
    assert result["confidence_score"] == expected


def test_precedent_similarity_normalizes_0_to_1():
    """Values in [0, 1] should be scaled to [0, 100]."""
    result = confidence_calc(
        evidence_strengths=["moderate"],
        fact_statuses=["corroborated"],
        witness_scores=[70],
        precedent_similarities=[0.5],
    )

    assert result["breakdown"]["precedents"] == 50.0


def test_precedent_similarity_accepts_0_to_100():
    """Values already in [0, 100] (but > 1) should be used as-is."""
    result = confidence_calc(
        evidence_strengths=["moderate"],
        fact_statuses=["corroborated"],
        witness_scores=[70],
        precedent_similarities=[75.0],
    )

    assert result["breakdown"]["precedents"] == 75.0


def test_invalid_labels_ignored():
    """Unrecognized strength/status labels are silently dropped."""
    result = confidence_calc(
        evidence_strengths=["strong", "bogus_label"],
        fact_statuses=["verified", "unknown_status"],
        witness_scores=[80],
        precedent_similarities=[0.9],
    )

    # Only the valid labels contribute to averages
    assert result["breakdown"]["evidence"] == 90.0  # only "strong"
    assert result["breakdown"]["facts"] == 95.0  # only "verified"
    assert result["confidence_score"] > 0
