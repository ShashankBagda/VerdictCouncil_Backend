"""What-If Controller for Contestable Judgment Mode.

Deep-clones a completed CaseState, applies the judge's modification,
determines the correct mid-pipeline re-entry point, and re-runs
downstream agents. Stores results with structured diff view.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import uuid
from typing import Any

from src.pipeline.runner import AGENT_ORDER, PipelineRunner
from src.services.whatif_controller.diff_engine import generate_diff
from src.shared.case_state import CaseState

logger = logging.getLogger(__name__)


class WhatIfController:
    """Manages what-if scenario execution and stability scoring."""

    # Maps modification types to the earliest agent that must re-run
    CHANGE_IMPACT_MAP: dict[str, str] = {
        "fact_toggle": "argument-construction",  # Agent 7
        "evidence_exclusion": "evidence-analysis",  # Agent 3
        "witness_credibility": "argument-construction",  # Agent 7
        "legal_interpretation": "legal-knowledge",  # Agent 6
    }

    def __init__(self, pipeline_runner: PipelineRunner) -> None:
        self._pipeline_runner = pipeline_runner

    async def create_scenario(
        self,
        case_state: CaseState,
        modification_type: str,
        modification_payload: dict[str, Any],
    ) -> CaseState:
        """Deep-clone a completed CaseState, apply modification, and re-run downstream agents.

        Args:
            case_state: The original completed CaseState.
            modification_type: One of the CHANGE_IMPACT_MAP keys.
            modification_payload: Describes the specific change to apply.

        Returns:
            The modified CaseState with a new verdict after re-running
            the pipeline from the appropriate re-entry point.
        """
        if modification_type not in self.CHANGE_IMPACT_MAP:
            raise ValueError(
                f"Unknown modification_type '{modification_type}'. "
                f"Must be one of: {list(self.CHANGE_IMPACT_MAP.keys())}"
            )

        # Deep-clone the case state
        cloned = CaseState(**copy.deepcopy(case_state.model_dump()))

        # Apply the modification
        cloned = self._apply_modification(cloned, modification_type, modification_payload)

        # Assign new run_id and link to original
        cloned.parent_run_id = case_state.run_id
        cloned.run_id = str(uuid.uuid4())

        # Determine which agents to re-run
        start_agent = self.CHANGE_IMPACT_MAP[modification_type]
        start_index = AGENT_ORDER.index(start_agent)
        agents_to_run = AGENT_ORDER[start_index:]

        logger.info(
            "What-if scenario: re-running agents %s for case_id=%s, run_id=%s",
            agents_to_run,
            cloned.case_id,
            cloned.run_id,
        )

        # Re-run the pipeline from the re-entry point onwards
        state = cloned
        for agent_name in agents_to_run:
            state = await self._pipeline_runner._run_agent(agent_name, state)

        return state

    def _apply_modification(
        self,
        state: CaseState,
        modification_type: str,
        payload: dict[str, Any],
    ) -> CaseState:
        """Apply the judge's modification to the cloned CaseState.

        Dispatches to the appropriate handler based on modification_type.
        """
        if modification_type == "fact_toggle":
            return self._apply_fact_toggle(state, payload)
        elif modification_type == "evidence_exclusion":
            return self._apply_evidence_exclusion(state, payload)
        elif modification_type == "witness_credibility":
            return self._apply_witness_credibility(state, payload)
        elif modification_type == "legal_interpretation":
            return self._apply_legal_interpretation(state, payload)
        return state

    def _apply_fact_toggle(self, state: CaseState, payload: dict[str, Any]) -> CaseState:
        """Toggle a fact's status between agreed/disputed.

        Expected payload: {fact_id: str, new_status: str}
        """
        if not state.extracted_facts:
            return state

        fact_id = payload.get("fact_id")
        new_status = payload.get("new_status", "disputed")

        facts = state.extracted_facts
        if isinstance(facts, dict) and "facts" in facts:
            for fact in facts["facts"]:
                if isinstance(fact, dict) and fact.get("id") == fact_id:
                    fact["status"] = new_status
                    break

        state.extracted_facts = facts
        return state

    def _apply_evidence_exclusion(self, state: CaseState, payload: dict[str, Any]) -> CaseState:
        """Exclude a piece of evidence from analysis.

        Expected payload: {evidence_id: str, exclude: bool}
        """
        if not state.evidence_analysis:
            return state

        evidence_id = payload.get("evidence_id")
        exclude = payload.get("exclude", True)

        analysis = state.evidence_analysis
        if isinstance(analysis, dict) and "evidence_items" in analysis:
            for item in analysis["evidence_items"]:
                if isinstance(item, dict) and item.get("id") == evidence_id:
                    item["excluded"] = exclude
                    if exclude:
                        item["exclusion_reason"] = payload.get(
                            "reason", "Excluded by judge via what-if scenario"
                        )
                    break

        state.evidence_analysis = analysis
        return state

    def _apply_witness_credibility(self, state: CaseState, payload: dict[str, Any]) -> CaseState:
        """Adjust a witness's credibility score.

        Expected payload: {witness_id: str, new_credibility_score: int}
        """
        if not state.witnesses:
            return state

        witness_id = payload.get("witness_id")
        new_score = payload.get("new_credibility_score")

        witnesses = state.witnesses
        if isinstance(witnesses, dict) and "witnesses" in witnesses:
            for witness in witnesses["witnesses"]:
                if isinstance(witness, dict) and witness.get("id") == witness_id:
                    witness["credibility_score"] = new_score
                    break

        state.witnesses = witnesses
        return state

    def _apply_legal_interpretation(self, state: CaseState, payload: dict[str, Any]) -> CaseState:
        """Change a legal interpretation or rule application.

        Expected payload: {rule_index: int, new_application: str}
        or {rule_id: str, new_application: str}
        """
        rule_id = payload.get("rule_id")
        rule_index = payload.get("rule_index")
        new_application = payload.get("new_application")

        if rule_id is not None:
            for rule in state.legal_rules:
                if isinstance(rule, dict) and rule.get("id") == rule_id:
                    rule["application"] = new_application
                    break
        elif rule_index is not None and 0 <= rule_index < len(state.legal_rules):
            rule = state.legal_rules[rule_index]
            if isinstance(rule, dict):
                rule["application"] = new_application

        return state

    async def compute_stability_score(
        self,
        case_state: CaseState,
        n: int = 5,
    ) -> dict[str, Any]:
        """Compute the stability score by running N perturbation scenarios.

        Identifies the top N perturbable inputs (binary facts, excludable
        evidence) and runs what-if scenarios in parallel. The stability
        score reflects how many perturbations leave the verdict unchanged.

        Args:
            case_state: The original completed CaseState.
            n: Number of perturbations to test.

        Returns:
            A dict with score, classification, perturbation_count,
            perturbations_held, and details.
        """
        perturbations = self._identify_perturbations(case_state, n)

        if not perturbations:
            return {
                "score": 100,
                "classification": "stable",
                "perturbation_count": 0,
                "perturbations_held": 0,
                "details": [],
            }

        # Run all perturbation scenarios in parallel
        tasks = [
            self.create_scenario(case_state, p["modification_type"], p["payload"])
            for p in perturbations
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Evaluate results
        details: list[dict[str, Any]] = []
        perturbations_held = 0

        for perturbation, result in zip(perturbations, results, strict=True):
            if isinstance(result, Exception):
                logger.error(
                    "Perturbation scenario failed: %s — %s",
                    perturbation["description"],
                    result,
                )
                detail = {
                    "description": perturbation["description"],
                    "modification_type": perturbation["modification_type"],
                    "error": str(result),
                    "verdict_held": False,
                }
            else:
                diff = generate_diff(case_state, result)
                verdict_held = not diff["verdict_changed"]
                if verdict_held:
                    perturbations_held += 1

                detail = {
                    "description": perturbation["description"],
                    "modification_type": perturbation["modification_type"],
                    "verdict_held": verdict_held,
                    "confidence_delta": diff["confidence_delta"],
                }

            details.append(detail)

        total = len(perturbations)
        score = int((perturbations_held / total) * 100) if total > 0 else 100

        if score >= 85:
            classification = "stable"
        elif score >= 60:
            classification = "moderately_sensitive"
        else:
            classification = "highly_sensitive"

        return {
            "score": score,
            "classification": classification,
            "perturbation_count": total,
            "perturbations_held": perturbations_held,
            "details": details,
        }

    def _identify_perturbations(self, case_state: CaseState, n: int) -> list[dict[str, Any]]:
        """Identify the top N perturbable inputs from the CaseState.

        Looks for binary facts (agreed/disputed), excludable evidence,
        and adjustable witness credibility scores.
        """
        perturbations: list[dict[str, Any]] = []

        # Fact toggles
        if case_state.extracted_facts and isinstance(case_state.extracted_facts, dict):
            facts = case_state.extracted_facts.get("facts", [])
            for fact in facts:
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

        # Evidence exclusions
        if case_state.evidence_analysis and isinstance(case_state.evidence_analysis, dict):
            evidence_items = case_state.evidence_analysis.get("evidence_items", [])
            for item in evidence_items:
                if isinstance(item, dict) and not item.get("excluded", False):
                    perturbations.append(
                        {
                            "modification_type": "evidence_exclusion",
                            "payload": {
                                "evidence_id": item.get("id"),
                                "exclude": True,
                            },
                            "description": (f"Exclude evidence '{item.get('id', 'unknown')}'"),
                        }
                    )

        # Limit to top N perturbations
        return perturbations[:n]
