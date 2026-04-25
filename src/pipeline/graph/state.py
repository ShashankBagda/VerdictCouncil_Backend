"""GraphState TypedDict and CaseState reducer for the LangGraph pipeline."""

from __future__ import annotations

from typing import Annotated, Any

from typing_extensions import TypedDict

from src.pipeline.graph.schemas import (
    AuditOutput,
    IntakeOutput,
    ResearchOutput,
    ResearchPart,
    SynthesisOutput,
)
from src.shared.case_state import AuditEntry, CaseState


def _merge_retry_counts(base: dict[str, int], update: dict[str, int]) -> dict[str, int]:
    """Reducer for retry_counts: union dicts, keeping the max count per agent.

    The retry-router nodes write partial dicts (one agent key at a time). The
    max-per-key rule ensures a stale parallel path can never reset a counter
    already advanced by another branch.
    """
    merged = dict(base)
    for k, v in update.items():
        merged[k] = max(merged.get(k, 0), v)
    return merged


def _merge_dicts(base: dict, update: dict) -> dict:
    """Reducer for dict fields written by parallel branches: shallow union.

    Later writes for the same key win. Prevents parallel Gate-2 nodes from
    clobbering each other's entries via last-writer-wins.
    """
    return {**base, **update}


def _merge_research_parts(
    base: dict[str, ResearchPart],
    update: dict[str, ResearchPart],
) -> dict[str, ResearchPart]:
    """Reducer for the dict-keyed `research_parts` accumulator (1.A1.5 / SA F-2).

    Each research subagent writes `{"research_parts": {scope: ResearchPart(...)}}`.
    The reducer is a shallow dict union keyed by scope name, so re-running a
    single scope (e.g. judge-driven rerun) naturally overwrites that key
    without a sentinel reset. Out-of-band wholesale resets go through
    `update_state(..., values, as_node=...)` with `Overwrite`.
    """
    return {**base, **update}


def _merge_case(base: CaseState, update: CaseState) -> CaseState:
    """Reducer for the 'case' field in GraphState.

    Sequential nodes: update contains the full new state; changed fields are applied.
    Parallel Gate-2 nodes: each update only modifies its owned fields; other fields
    are unchanged from the node's input. Merge rules:

    1. Field unchanged (base == update) → keep base (no-op).
    2. Base is None / [] / {} and update has a value → apply update (new data written).
    3. Base has a value and update is None / [] / {} → keep base (parallel node didn't own it).
    4. Both differ and both non-empty → apply update (sequential override, last-writer-wins).

    audit_log is always extended with entries not already present in base (dedup by equality).
    """
    base_data = base.model_dump()
    update_data = update.model_dump()
    merged: dict[str, Any] = dict(base_data)

    _empty: tuple[Any, ...] = (None, [], {})

    for field, new_val in update_data.items():
        if field == "audit_log":
            continue  # handled below

        base_val = base_data.get(field)

        if new_val == base_val:
            continue

        if base_val in _empty:
            # base is unset — apply whatever update contributes
            merged[field] = new_val
        elif new_val in _empty:
            # update is unset — parallel branch didn't own this field; keep base
            pass
        else:
            # both non-empty and different — sequential update, take latest
            merged[field] = new_val

    # audit_log: extend base with entries not already present (Pydantic __eq__ dedup)
    base_entries: list[AuditEntry] = base.audit_log or []
    update_entries: list[AuditEntry] = update.audit_log or []
    new_entries = [e for e in update_entries if e not in base_entries]
    merged["audit_log"] = base_entries + new_entries

    return CaseState(**merged)


class GraphState(TypedDict):
    """Full graph state for the VerdictCouncil LangGraph pipeline.

    'case' uses a custom reducer to safely merge parallel Gate-2 branch outputs.
    All other fields use LangGraph's default last-writer-wins semantics.
    """

    case: Annotated[CaseState, _merge_case]

    # Passed through from the dispatch call — used by nodes for MLflow and SSE
    run_id: str

    # Per-agent extra instructions injected at retry time
    # Keys are agent names (e.g. "fact-reconstruction")
    extra_instructions: dict[str, str]

    # Retry counter per agent — incremented by retry-router nodes at the routing boundary
    retry_counts: Annotated[dict[str, int], _merge_retry_counts]

    # Set by any node that escalates or halts the pipeline
    halt: dict[str, Any] | None

    # MLflow run IDs written by each node after its agent_run() context manager exits
    # Value is (mlflow_run_id, experiment_id)
    mlflow_run_ids: Annotated[dict[str, tuple[str, str]], _merge_dicts]

    # Research fan-out accumulator (1.A1.5). Subagents write
    # `{scope: ResearchPart(...)}`; the reducer dict-merges by scope so
    # parallel branches and judge-driven reruns coexist cleanly.
    research_parts: Annotated[dict[str, ResearchPart], _merge_research_parts]

    # Output of `research_join_node` (1.A1.5). Default LWW semantics — the
    # join writes once per pipeline run and a re-entered join overwrites.
    research_output: ResearchOutput | None

    # Phase-output state slots (1.A1.7). Written by `make_phase_node(...)`
    # via the factory's `{phase}_output` return shape; consumed by gate
    # pauses (snapshot-for-judge) and by Sprint 2 case-state integration.
    # LWW semantics — each phase writes once per pipeline run.
    intake_output: IntakeOutput | None
    synthesis_output: SynthesisOutput | None
    audit_output: AuditOutput | None

    # Carrier for the judge's gate decision (1.A1.7). The pause node writes
    # the `interrupt()` return value here; the apply node reads it, derives
    # the next-node target, and clears the slot. LWW.
    pending_action: dict[str, Any] | None

    # True when resuming from a checkpoint (skip already-completed gates)
    is_resume: bool

    # When set, graph execution begins from this node instead of case_processing
    start_agent: str | None
