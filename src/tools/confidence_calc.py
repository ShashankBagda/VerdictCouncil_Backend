"""Confidence calculation tool for VerdictCouncil verdict recommendations.

Pure calculation -- no LLM calls. Computes a weighted confidence score
from evidence strengths, fact statuses, witness credibility scores,
and precedent similarity scores.
"""

import logging

logger = logging.getLogger(__name__)

# Default weight distribution
_DEFAULT_WEIGHTS = {
    "evidence": 0.30,
    "facts": 0.25,
    "witnesses": 0.20,
    "precedents": 0.25,
}

# Strength/status label-to-numeric mappings
_EVIDENCE_STRENGTH_MAP: dict[str, int] = {
    "strong": 90,
    "moderate": 65,
    "weak": 35,
    "insufficient": 10,
}

_FACT_STATUS_MAP: dict[str, int] = {
    "verified": 95,
    "corroborated": 85,
    "disputed": 40,
    "unverified": 20,
    "contradicted": 5,
}


def _label_to_score(label: str, mapping: dict[str, int]) -> int | None:
    """Convert a textual label to a numeric score using a mapping."""
    return mapping.get(label.lower().strip()) if isinstance(label, str) else None


def _safe_average(scores: list[float | int]) -> float:
    """Compute the average of valid numeric scores in [0, 100]."""
    valid = [s for s in scores if isinstance(s, (int, float)) and 0 <= s <= 100]
    return sum(valid) / len(valid) if valid else 0.0


def confidence_calc(
    evidence_strengths: list[str],
    fact_statuses: list[str],
    witness_scores: list[int],
    precedent_similarities: list[float],
) -> dict:
    """Calculate verdict confidence score from component inputs.

    Uses weighted scoring:
        - Evidence:   30%
        - Facts:      25%
        - Witnesses:  20%
        - Precedents: 25%

    Args:
        evidence_strengths: List of strength labels for each evidence item.
            Valid values: "strong", "moderate", "weak", "insufficient".
        fact_statuses: List of status labels for each extracted fact.
            Valid values: "verified", "corroborated", "disputed",
            "unverified", "contradicted".
        witness_scores: List of witness credibility scores (0-100).
        precedent_similarities: List of precedent similarity scores
            (0.0-1.0 or 0-100).

    Returns:
        Dictionary with:
            - confidence_score (int): Overall score 0-100.
            - breakdown (dict): Per-component average scores.
            - classification (str): "High" (80-100), "Medium" (60-79),
              or "Low" (0-59).
    """
    weights = _DEFAULT_WEIGHTS

    # Convert evidence strength labels to numeric scores
    evidence_numeric = [
        s
        for label in evidence_strengths
        if (s := _label_to_score(label, _EVIDENCE_STRENGTH_MAP)) is not None
    ]

    # Convert fact status labels to numeric scores
    fact_numeric = [
        s for label in fact_statuses if (s := _label_to_score(label, _FACT_STATUS_MAP)) is not None
    ]

    # Normalize precedent similarities: if values are in [0, 1], scale to [0, 100]
    precedent_numeric: list[float] = []
    for val in precedent_similarities:
        if isinstance(val, (int, float)):
            if 0 <= val <= 1.0:
                precedent_numeric.append(val * 100)
            elif 0 <= val <= 100:
                precedent_numeric.append(float(val))

    # Compute component averages
    evidence_avg = _safe_average(evidence_numeric)
    facts_avg = _safe_average(fact_numeric)
    witnesses_avg = _safe_average(witness_scores)
    precedents_avg = _safe_average(precedent_numeric)

    breakdown = {
        "evidence": round(evidence_avg, 1),
        "facts": round(facts_avg, 1),
        "witnesses": round(witnesses_avg, 1),
        "precedents": round(precedents_avg, 1),
    }

    # Weighted sum
    raw_score = (
        evidence_avg * weights["evidence"]
        + facts_avg * weights["facts"]
        + witnesses_avg * weights["witnesses"]
        + precedents_avg * weights["precedents"]
    )
    confidence_score = max(0, min(100, round(raw_score)))

    # Classification bands
    if confidence_score >= 80:
        classification = "High"
    elif confidence_score >= 60:
        classification = "Medium"
    else:
        classification = "Low"

    logger.info(
        "Confidence calculated: %d (%s) — evidence=%.1f, facts=%.1f, witnesses=%.1f, precedents=%.1f",  # noqa: E501
        confidence_score,
        classification,
        evidence_avg,
        facts_avg,
        witnesses_avg,
        precedents_avg,
    )

    return {
        "confidence_score": confidence_score,
        "breakdown": breakdown,
        "classification": classification,
    }
