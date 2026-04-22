"""Generates structured diff between original and modified verdicts."""

from __future__ import annotations

from typing import Any

from src.shared.case_state import CaseState


def generate_diff(original: CaseState, modified: CaseState) -> dict[str, Any]:
    """Compare two CaseStates and produce a structured diff.

    Returns a dict containing:
        - fact_changes: list of facts whose status changed
        - evidence_changes: list of evidence added/removed/excluded
        - argument_deltas: comparison of argument strengths
        - reasoning_diff: text diff of deliberation reasoning
        - verdict_changed: bool
        - confidence_delta: int (new - original)
        - original_verdict: summary
        - modified_verdict: summary
    """
    return {
        "fact_changes": _diff_facts(original, modified),
        "evidence_changes": _diff_evidence(original, modified),
        "argument_deltas": _diff_arguments(original, modified),
        "reasoning_diff": _diff_reasoning(original, modified),
        "verdict_changed": _verdict_changed(original, modified),
        "confidence_delta": _confidence_delta(original, modified),
        "original_verdict": _extract_verdict_summary(original),
        "modified_verdict": _extract_verdict_summary(modified),
    }


def _diff_facts(original: CaseState, modified: CaseState) -> list[dict[str, Any]]:
    """Identify facts whose status changed between original and modified."""
    changes: list[dict[str, Any]] = []

    orig_facts = _get_facts_map(original)
    mod_facts = _get_facts_map(modified)

    all_ids = set(orig_facts.keys()) | set(mod_facts.keys())
    for fact_id in all_ids:
        orig = orig_facts.get(fact_id)
        mod = mod_facts.get(fact_id)

        if orig and mod:
            orig_status = orig.get("status")
            mod_status = mod.get("status")
            if orig_status != mod_status:
                changes.append(
                    {
                        "fact_id": fact_id,
                        "original_status": orig_status,
                        "modified_status": mod_status,
                        "description": orig.get("description", ""),
                    }
                )
        elif orig and not mod:
            changes.append(
                {
                    "fact_id": fact_id,
                    "change": "removed",
                    "description": orig.get("description", ""),
                }
            )
        elif mod and not orig:
            changes.append(
                {
                    "fact_id": fact_id,
                    "change": "added",
                    "description": mod.get("description", ""),
                }
            )

    return changes


def _diff_evidence(original: CaseState, modified: CaseState) -> list[dict[str, Any]]:
    """Identify evidence items that were added, removed, or excluded."""
    changes: list[dict[str, Any]] = []

    orig_items = _get_evidence_map(original)
    mod_items = _get_evidence_map(modified)

    all_ids = set(orig_items.keys()) | set(mod_items.keys())
    for ev_id in all_ids:
        orig = orig_items.get(ev_id)
        mod = mod_items.get(ev_id)

        if orig and mod:
            orig_excluded = orig.get("excluded", False)
            mod_excluded = mod.get("excluded", False)
            if orig_excluded != mod_excluded:
                changes.append(
                    {
                        "evidence_id": ev_id,
                        "change": "excluded" if mod_excluded else "re-included",
                    }
                )
        elif orig and not mod:
            changes.append({"evidence_id": ev_id, "change": "removed"})
        elif mod and not orig:
            changes.append({"evidence_id": ev_id, "change": "added"})

    return changes


def _diff_arguments(original: CaseState, modified: CaseState) -> list[dict[str, Any]]:
    """Compare argument strengths between original and modified states."""
    deltas: list[dict[str, Any]] = []

    orig_args = original.arguments or {}
    mod_args = modified.arguments or {}

    if not isinstance(orig_args, dict) or not isinstance(mod_args, dict):
        return deltas

    # Compare sides if structured as {prosecution: {...}, defense: {...}}
    for side in ("prosecution", "defense", "claimant", "respondent"):
        orig_side = orig_args.get(side, {})
        mod_side = mod_args.get(side, {})

        if not isinstance(orig_side, dict) or not isinstance(mod_side, dict):
            continue

        orig_strength = orig_side.get("overall_strength")
        mod_strength = mod_side.get("overall_strength")

        if orig_strength is not None or mod_strength is not None:
            deltas.append(
                {
                    "side": side,
                    "original_strength": orig_strength,
                    "modified_strength": mod_strength,
                }
            )

    return deltas


def _diff_reasoning(original: CaseState, modified: CaseState) -> dict[str, Any]:
    """Compare deliberation reasoning between original and modified."""
    orig_delib = original.deliberation
    mod_delib = modified.deliberation

    return {
        "original": orig_delib.preliminary_conclusion if orig_delib else None,
        "modified": mod_delib.preliminary_conclusion if mod_delib else None,
        "original_confidence": orig_delib.confidence_score if orig_delib else None,
        "modified_confidence": mod_delib.confidence_score if mod_delib else None,
    }


def _verdict_changed(original: CaseState, modified: CaseState) -> bool:
    """Check whether the verdict recommendation changed."""
    orig_verdict = _extract_verdict_summary(original)
    mod_verdict = _extract_verdict_summary(modified)

    if orig_verdict is None and mod_verdict is None:
        return False

    if orig_verdict is None or mod_verdict is None:
        return True

    return orig_verdict.get("recommendation_type") != mod_verdict.get(
        "recommendation_type"
    ) or orig_verdict.get("recommended_outcome") != mod_verdict.get("recommended_outcome")


def _confidence_delta(original: CaseState, modified: CaseState) -> int:
    """Calculate the confidence score difference (modified - original)."""
    orig_score = _get_confidence(original)
    mod_score = _get_confidence(modified)
    return mod_score - orig_score


def _extract_verdict_summary(state: CaseState) -> dict[str, Any] | None:
    """Extract a summary of the verdict from CaseState."""
    verdict = state.verdict_recommendation
    if not verdict:
        return None

    return {
        "recommendation_type": verdict.recommendation_type,
        "recommended_outcome": verdict.recommended_outcome,
        "confidence_score": verdict.confidence_score,
    }


def _get_confidence(state: CaseState) -> int:
    """Extract confidence score from verdict or deliberation."""
    if state.verdict_recommendation:
        return state.verdict_recommendation.confidence_score

    if state.deliberation and state.deliberation.confidence_score is not None:
        return state.deliberation.confidence_score

    return 0


def _get_facts_map(state: CaseState) -> dict[str, dict]:
    """Build a fact_id -> fact dict from CaseState."""
    if not state.extracted_facts:
        return {}

    return {
        f.get("id", str(i)): f
        for i, f in enumerate(state.extracted_facts.facts)
        if isinstance(f, dict)
    }


def _get_evidence_map(state: CaseState) -> dict[str, dict]:
    """Build an evidence_id -> evidence dict from CaseState."""
    if not state.evidence_analysis:
        return {}

    return {
        item.get("id", str(i)): item
        for i, item in enumerate(state.evidence_analysis.evidence_items)
        if isinstance(item, dict)
    }
