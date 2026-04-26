"""Sprint 4 4.A5.2 — fork-driven stability scoring.

Fans out N forks via :func:`create_whatif_fork` and drives each to
terminal via :func:`drive_whatif_to_terminal`, then aggregates the
hold-rate. Score / classification contract matches the prior in-process
deep-clone implementation so ``StabilityScore`` rows and the API
response stay byte-stable.

The fork primitive seeds at ``research_join``, so each perturbation
re-runs synthesis + audit against the modified case — slower than the
legacy mid-pipeline re-entry, but the explicit design of the new
primitive: every fork is a complete hypothetical trace in LangSmith.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from langgraph.graph.state import CompiledStateGraph

from src.services.whatif.diff import generate_diff
from src.services.whatif.fork import (
    WhatIfModification,
    create_whatif_fork,
    drive_whatif_to_terminal,
)
from src.shared.case_state import CaseState

logger = logging.getLogger(__name__)


_STABLE_THRESHOLD = 85
_MODERATE_THRESHOLD = 60


def classify(score: int) -> str:
    """Map a 0–100 stability score to its qualitative band.

    Bands match the legacy controller so existing dashboards / API
    clients see identical labels.
    """
    if score >= _STABLE_THRESHOLD:
        return "stable"
    if score >= _MODERATE_THRESHOLD:
        return "moderately_sensitive"
    return "highly_sensitive"


def identify_perturbations(case_state: CaseState, n: int) -> list[dict[str, Any]]:
    """Pick up to N binary perturbations from a terminal CaseState.

    Looks for facts with a flippable status (agreed ↔ disputed) and for
    evidence items that are currently included (so excluding them is a
    meaningful change). Returns a list of dicts shaped for direct
    consumption by :class:`WhatIfModification` plus a human-readable
    description for the API response.
    """
    perturbations: list[dict[str, Any]] = []

    if case_state.extracted_facts:
        for fact in case_state.extracted_facts.facts:
            if isinstance(fact, dict) and fact.get("status") in ("agreed", "disputed"):
                new_status = "disputed" if fact["status"] == "agreed" else "agreed"
                perturbations.append(
                    {
                        "modification_type": "fact_toggle",
                        "payload": {
                            "fact_id": fact.get("id"),
                            "new_status": new_status,
                        },
                        "description": (
                            f"Toggle fact '{fact.get('id', 'unknown')}' "
                            f"from {fact['status']} to {new_status}"
                        ),
                    }
                )

    if case_state.evidence_analysis:
        for item in case_state.evidence_analysis.evidence_items:
            if isinstance(item, dict) and not item.get("excluded", False):
                perturbations.append(
                    {
                        "modification_type": "evidence_exclusion",
                        "payload": {
                            "evidence_id": item.get("id"),
                            "exclude": True,
                        },
                        "description": f"Exclude evidence '{item.get('id', 'unknown')}'",
                    }
                )

    return perturbations[:n]


async def _run_one_perturbation(
    *,
    graph: CompiledStateGraph[Any],
    case_id: str,
    fork_judge_id: str,
    perturbation: dict[str, Any],
    parent_run_id: str | None,
) -> CaseState:
    """Fork + drive one perturbation, returning the fork's terminal CaseState.

    Each call carves a fresh fork thread off the case_id thread; the
    parallel ``asyncio.gather`` in :func:`compute_stability_score` is
    safe because every fork has a unique thread_id (judge + uuid in
    :func:`fork_thread_id_for`).
    """
    modification = WhatIfModification(
        modification_type=perturbation["modification_type"],
        payload=perturbation["payload"],
    )
    fork_thread_id = await create_whatif_fork(
        graph=graph,
        case_id=case_id,
        judge_id=fork_judge_id,
        modifications=[modification],
        parent_run_id=parent_run_id,
    )
    await drive_whatif_to_terminal(graph=graph, fork_thread_id=fork_thread_id)
    snap = await graph.aget_state({"configurable": {"thread_id": fork_thread_id}})
    return cast(CaseState, snap.values["case"])


async def compute_stability_score(
    *,
    graph: CompiledStateGraph[Any],
    case_id: str,
    case_state: CaseState,
    n: int,
    fork_judge_id: str,
    parent_run_id: str | None = None,
) -> dict[str, Any]:
    """Run N parallel fork perturbations and aggregate their hold-rate.

    Args:
        graph: The compiled saver-bound graph (``runner._graph``).
        case_id: The original case's thread_id.
        case_state: The terminal CaseState used as the diff baseline.
            (Read from the DB in the route — the fork primitive reads
            its own seed off the saver thread directly, so this argument
            is *only* for the post-run diff.)
        n: Maximum number of perturbations to run.
        fork_judge_id: Synthetic identity for the fork thread keys. The
            stability route uses ``f"stab-{stability_id}"`` so forks are
            isolated from any judge's manual what-if scenarios.
        parent_run_id: Stamped onto each fork's checkpoint metadata for
            LangSmith trace navigation back to the original run.

    Returns:
        ``{"score", "classification", "perturbation_count",
        "perturbations_held", "details"}`` — same shape as the legacy
        controller so callers see no contract change.
    """
    perturbations = identify_perturbations(case_state, n)

    if not perturbations:
        return {
            "score": 100,
            "classification": "stable",
            "perturbation_count": 0,
            "perturbations_held": 0,
            "details": [],
        }

    tasks = [
        _run_one_perturbation(
            graph=graph,
            case_id=case_id,
            fork_judge_id=fork_judge_id,
            perturbation=p,
            parent_run_id=parent_run_id,
        )
        for p in perturbations
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    details: list[dict[str, Any]] = []
    perturbations_held = 0
    for perturbation, result in zip(perturbations, results, strict=True):
        if isinstance(result, BaseException):
            logger.error(
                "stability perturbation failed: %s — %s",
                perturbation["description"],
                result,
            )
            details.append(
                {
                    "description": perturbation["description"],
                    "modification_type": perturbation["modification_type"],
                    "error": str(result),
                    "verdict_held": False,
                }
            )
            continue

        diff = generate_diff(case_state, result)
        verdict_held = not diff["analysis_changed"]
        if verdict_held:
            perturbations_held += 1
        details.append(
            {
                "description": perturbation["description"],
                "modification_type": perturbation["modification_type"],
                "verdict_held": verdict_held,
                "confidence_delta": diff["confidence_delta"],
            }
        )

    total = len(perturbations)
    score = int((perturbations_held / total) * 100) if total > 0 else 100
    return {
        "score": score,
        "classification": classify(score),
        "perturbation_count": total,
        "perturbations_held": perturbations_held,
        "details": details,
    }
