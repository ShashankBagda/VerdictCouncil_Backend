"""Sprint 4 4.A5.1 — LangGraph-native What-If fork primitive.

Saver-driven fork:

1. Read the terminal CaseState from the original case's thread
   (``thread_id == case_id``).
2. Apply the judge's modifications to the in-memory CaseState (e.g.
   exclude an evidence item, flip a fact's status).
3. Seed a fresh fork thread (``thread_id = case_id-whatif-judge_id-uuid``)
   via ``aupdate_state(fork_config, {"case": Overwrite(modified), …},
   as_node="research_join")`` so the fork resumes at the gate2 pause —
   synthesis re-runs against the modified state on advance.
4. Stamp ``parent_run_id`` + ``parent_thread_id`` in the checkpoint
   metadata for LangSmith trace navigation.

R-10 isolation has two layers:

- **Thread-key**: judge_id is part of the fork's thread_id, so judge B
  cannot share a saver key with judge A. This prevents accidental
  collisions, not directed access.
- **API**: ``what_if.py`` enforces that the requesting user owns the
  ``WhatIfScenario`` row before returning saver state. That is the
  authoritative isolation gate; the thread_id format is the structural
  hint that backs it.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Literal, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from src.pipeline.graph.state import Overwrite
from src.shared.case_state import CaseState

logger = logging.getLogger(__name__)


ModificationType = Literal[
    "fact_toggle",
    "evidence_exclusion",
    "witness_credibility",
    "legal_interpretation",
]

_VALID_MOD_TYPES: frozenset[str] = frozenset(
    ("fact_toggle", "evidence_exclusion", "witness_credibility", "legal_interpretation")
)


@dataclass(frozen=True)
class WhatIfModification:
    """A single judge-driven modification to apply before forking.

    ``modification_type`` is gate-checked at construction so the fork
    primitive cannot be handed an unknown payload shape that would
    silently no-op.

    Payload shapes:

    - ``fact_toggle``: ``{fact_id: str, new_status: "agreed" | "disputed"}``
    - ``evidence_exclusion``: ``{evidence_id: str, reason?: str}``
    - ``witness_credibility``: ``{witness_id: str, new_credibility_score: int}``
    - ``legal_interpretation``: ``{rule_id?: str, rule_index?: int,
      new_application: str}``
    """

    modification_type: ModificationType
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if self.modification_type not in _VALID_MOD_TYPES:
            raise ValueError(
                f"WhatIfModification: unknown modification_type "
                f"{self.modification_type!r}; expected one of {sorted(_VALID_MOD_TYPES)}"
            )


def _apply_evidence_exclusion(case: CaseState, payload: dict[str, Any]) -> CaseState:
    if not case.evidence_analysis:
        return case
    evidence_id = payload.get("evidence_id")
    reason = payload.get("reason", "Excluded by judge via what-if scenario")
    items = list(case.evidence_analysis.evidence_items)
    for i, item in enumerate(items):
        if isinstance(item, dict) and item.get("id") == evidence_id:
            items[i] = {**item, "excluded": True, "exclusion_reason": reason}
    return case.model_copy(
        update={
            "evidence_analysis": case.evidence_analysis.model_copy(update={"evidence_items": items})
        }
    )


def _apply_fact_toggle(case: CaseState, payload: dict[str, Any]) -> CaseState:
    if not case.extracted_facts:
        return case
    fact_id = payload.get("fact_id")
    new_status = payload.get("new_status", "disputed")
    facts = list(case.extracted_facts.facts)
    for i, fact in enumerate(facts):
        if isinstance(fact, dict) and fact.get("id") == fact_id:
            facts[i] = {**fact, "status": new_status}
    return case.model_copy(
        update={"extracted_facts": case.extracted_facts.model_copy(update={"facts": facts})}
    )


def _apply_witness_credibility(case: CaseState, payload: dict[str, Any]) -> CaseState:
    if not case.witnesses:
        return case
    witness_id = payload.get("witness_id")
    new_score = payload.get("new_credibility_score")
    witnesses = list(case.witnesses.witnesses)
    for i, witness in enumerate(witnesses):
        if isinstance(witness, dict) and witness.get("id") == witness_id:
            witnesses[i] = {**witness, "credibility_score": new_score}
    return case.model_copy(
        update={"witnesses": case.witnesses.model_copy(update={"witnesses": witnesses})}
    )


def _apply_legal_interpretation(case: CaseState, payload: dict[str, Any]) -> CaseState:
    rule_id = payload.get("rule_id")
    rule_index = payload.get("rule_index")
    new_application = payload.get("new_application")
    rules = [dict(r) if isinstance(r, dict) else r for r in case.legal_rules]
    if rule_id is not None:
        for i, rule in enumerate(rules):
            if isinstance(rule, dict) and rule.get("id") == rule_id:
                rules[i] = {**rule, "application": new_application}
    elif rule_index is not None and 0 <= rule_index < len(rules):
        rule = rules[rule_index]
        if isinstance(rule, dict):
            rules[rule_index] = {**rule, "application": new_application}
    return case.model_copy(update={"legal_rules": rules})


_DISPATCH = {
    "evidence_exclusion": _apply_evidence_exclusion,
    "fact_toggle": _apply_fact_toggle,
    "witness_credibility": _apply_witness_credibility,
    "legal_interpretation": _apply_legal_interpretation,
}


def apply_modifications(case: CaseState, modifications: list[WhatIfModification]) -> CaseState:
    """Apply a list of judge modifications to a CaseState in order."""
    for mod in modifications:
        case = _DISPATCH[mod.modification_type](case, mod.payload)
    return case


def fork_thread_id_for(case_id: str, judge_id: str, fork_uuid: str | None = None) -> str:
    """Compose the fork's thread_id.

    Format: ``{case_id}-whatif-{judge_id}-{fork_uuid}``. The judge_id
    component scopes the saver key per-judge so a different judge
    cannot accidentally collide on the same fork uuid; API-layer
    ``created_by`` checks enforce directed access.
    """
    return f"{case_id}-whatif-{judge_id}-{fork_uuid or uuid.uuid4().hex}"


# Modifications all affect research-derived state (evidence weights,
# fact statuses, witness credibility, legal rule applications). Seeding
# the fork at ``research_join`` makes the fork resume at gate2_pause —
# advancing through gate2 then runs synthesis + audit against the
# modified state, which is the answer the what-if scenario wants. If a
# future modification targets pre-research state (e.g. a complexity
# label produced by intake), this map will need extending; for now all
# four modification types collapse to the same seed point.
_SEED_NODE_FOR_MOD: dict[str, str] = {
    "fact_toggle": "research_join",
    "evidence_exclusion": "research_join",
    "witness_credibility": "research_join",
    "legal_interpretation": "research_join",
}


def _seed_node(modifications: list[WhatIfModification]) -> str:
    """Pick the as_node seed point given the modification set."""
    nodes = {_SEED_NODE_FOR_MOD[m.modification_type] for m in modifications}
    if len(nodes) > 1:
        # Multiple seed points — drop back to the earliest so all
        # modifications land before their relevant phase re-runs. Today
        # every type maps to research_join; this branch is defensive
        # for future modification additions.
        return "research_join"
    return next(iter(nodes), "research_join")


async def create_whatif_fork(
    *,
    graph: CompiledStateGraph[Any],
    case_id: str,
    judge_id: str,
    modifications: list[WhatIfModification],
    parent_run_id: str | None = None,
) -> str:
    """Fork the case's checkpointed thread to explore a what-if scenario.

    Reads the terminal CaseState off the original thread, applies the
    judge's modifications, and seeds a fresh fork thread via
    :meth:`aupdate_state` with the modified case wrapped in
    :class:`Overwrite` so ``_merge_case`` cannot mask deliberate field
    clears. The fork's checkpoint metadata stamps ``parent_run_id`` and
    ``parent_thread_id`` for LangSmith trace navigation.

    Returns the fork's ``thread_id``. Caller is responsible for driving
    the fork to terminal via :func:`drive_whatif_to_terminal` (or by
    publishing it to a worker that does).

    Raises:
        RuntimeError: if the original thread has no terminal state — a
            what-if cannot fork from an empty case.
        ValueError: if any ``WhatIfModification`` has an unknown type.
            (Constructor-time check; raised here only on direct
            stringly-typed calls.)
    """
    if not modifications:
        raise ValueError("create_whatif_fork: at least one modification is required")

    orig_config = cast(RunnableConfig, {"configurable": {"thread_id": case_id}})
    orig_snap = await graph.aget_state(orig_config)
    orig_values = orig_snap.values if orig_snap else None
    if not orig_values or "case" not in orig_values:
        raise RuntimeError(
            f"create_whatif_fork: original thread {case_id!r} has no terminal "
            f"state — cannot fork from an empty case"
        )

    orig_case = orig_values["case"]
    if not isinstance(orig_case, CaseState):
        raise RuntimeError(
            f"create_whatif_fork: original thread state has unexpected case "
            f"type {type(orig_case).__name__}"
        )

    modified_case = apply_modifications(orig_case, modifications)

    fork_uuid = uuid.uuid4().hex
    fork_thread_id = fork_thread_id_for(case_id, judge_id, fork_uuid)
    fork_run_id = uuid.uuid4().hex
    parent_run_id = parent_run_id or orig_values.get("run_id")

    fork_config = cast(
        RunnableConfig,
        {
            "configurable": {"thread_id": fork_thread_id},
            "metadata": {
                "case_id": case_id,
                "run_id": fork_run_id,
                "parent_run_id": parent_run_id,
                "parent_thread_id": case_id,
                "judge_id": judge_id,
                "fork_uuid": fork_uuid,
                "whatif": True,
            },
        },
    )

    seed_payload: dict[str, Any] = {
        "case": Overwrite(modified_case),
        "run_id": fork_run_id,
        "extra_instructions": {},
        "retry_counts": {},
        "halt": None,
        "research_parts": orig_values.get("research_parts") or {},
        "research_output": orig_values.get("research_output"),
        "intake_output": orig_values.get("intake_output"),
        # Downstream phases must re-run against the modified state — clear
        # their slots so the gate-pause payloads aren't built off stale
        # synthesis / audit outputs from the original run.
        "synthesis_output": None,
        "audit_output": None,
        "pending_action": None,
        "is_resume": True,
        "start_agent": None,
        "retrieved_source_ids": orig_values.get("retrieved_source_ids") or {},
    }

    await graph.aupdate_state(fork_config, seed_payload, as_node=_seed_node(modifications))

    logger.info(
        "what-if fork created: thread_id=%s parent_thread_id=%s parent_run_id=%s "
        "judge_id=%s mods=%s",
        fork_thread_id,
        case_id,
        parent_run_id,
        judge_id,
        [m.modification_type for m in modifications],
    )
    return fork_thread_id


async def drive_whatif_to_terminal(
    *, graph: CompiledStateGraph[Any], fork_thread_id: str, max_steps: int = 8
) -> None:
    """Drive a fork forward through every remaining gate to END.

    Auto-advances through ``Command(resume={"action": "advance"})`` at
    each gate pause. The fork represents a hypothetical the judge has
    already agreed to; no further interactive review is required, so
    we accept every gate's default action.

    ``max_steps`` is a safety bound — in the current 4-gate topology
    a research_join-seeded fork needs at most three advances (gate2 →
    gate3 → gate4) before reaching END; the bound exists so a
    pathological loop fails loudly instead of spinning forever.
    """
    fork_config = cast(RunnableConfig, {"configurable": {"thread_id": fork_thread_id}})
    for _step in range(max_steps):
        snap = await graph.aget_state(fork_config)
        if not snap.next:
            return
        if any(t.interrupts for t in (snap.tasks or [])):
            await graph.ainvoke(Command(resume={"action": "advance"}), fork_config)
            continue
        # No interrupt pending but still has next nodes — drive a plain
        # invoke. Happens immediately after the seed when next is
        # ("gate2_pause",) and the interrupt hasn't fired yet.
        await graph.ainvoke(None, fork_config)
    raise RuntimeError(
        f"drive_whatif_to_terminal: fork {fork_thread_id!r} did not reach END "
        f"within {max_steps} steps — graph topology may have a non-terminating loop"
    )
