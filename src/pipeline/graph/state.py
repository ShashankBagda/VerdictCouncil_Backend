"""GraphState TypedDict and CaseState reducer for the LangGraph pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Generic, TypeVar

from typing_extensions import TypedDict

from src.pipeline.graph.schemas import (
    AuditOutput,
    IntakeOutput,
    ResearchOutput,
    ResearchPart,
    SynthesisOutput,
)
from src.shared.case_state import AuditEntry, CaseState

_T = TypeVar("_T")


@dataclass(frozen=True)
class Overwrite(Generic[_T]):  # noqa: UP046 — PEP 695 + frozen dataclass interplay is unsettled on 3.12; keep classic Generic syntax
    """Sentinel that bypasses the parallel-safe ``_merge_case`` semantics.

    The default ``_merge_case`` rule "if update is empty, keep base" is
    correct for parallel Gate-2 branches that only own a slice of the
    case — but it actively masks deliberate clears (e.g. the What-If
    fork seeding a stripped CaseState). Wrapping the update in
    :class:`Overwrite` instructs the reducer to take the new value
    verbatim, mirroring LangGraph's documented escape hatch for
    accumulator-style channels.

    Used by ``services/whatif/fork.create_whatif_fork`` when calling
    ``aupdate_state(fork_config, {"case": Overwrite(modified)}, …)``.
    """

    value: _T


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


def _merge_source_ids(
    base: dict[str, list[str]],
    update: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Reducer for `retrieved_source_ids` — dict-keyed by scope/phase (Sprint 3 3.B.5).

    Each phase or research-scope agent contributes a list of citation
    source_ids it retrieved via tool calls, keyed by its own
    scope/phase name (`law`, `evidence`, `intake`, `synthesis`, …).
    Re-running a single scope (judge-driven /rerun) overwrites that
    key — stale source_ids cannot accumulate across runs and the
    research_join validator's supported-citation check stays tight.

    Mirrors the dict-by-scope contract on `research_parts`: each scope
    owns its slot, so a rerun resets the scope without leaking
    stale data into other parallel branches.
    """
    return {**base, **update}


def _merge_case(base: CaseState, update: CaseState | Overwrite[CaseState]) -> CaseState:
    """Reducer for the 'case' field in GraphState.

    Sequential nodes: update contains the full new state; changed fields are applied.
    Parallel Gate-2 nodes: each update only modifies its owned fields; other fields
    are unchanged from the node's input. Merge rules:

    1. Field unchanged (base == update) → keep base (no-op).
    2. Base is None / [] / {} and update has a value → apply update (new data written).
    3. Base has a value and update is None / [] / {} → keep base (parallel node didn't own it).
    4. Both differ and both non-empty → apply update (sequential override, last-writer-wins).

    audit_log is always extended with entries not already present in base (dedup by equality).

    The :class:`Overwrite` sentinel short-circuits the merge — the
    wrapped value replaces base entirely. Used by What-If fork seeding
    where the judge's modifications must land verbatim, including
    deliberate field clears that the parallel-safe rules would otherwise
    discard.
    """
    if isinstance(update, Overwrite):
        return update.value

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

    # Passed through from the dispatch call — used by nodes for tracing and SSE
    run_id: str

    # Per-agent extra instructions injected at retry time
    # Keys are agent names (e.g. "fact-reconstruction")
    extra_instructions: dict[str, str]

    # Retry counter per agent — incremented by retry-router nodes at the routing boundary
    retry_counts: Annotated[dict[str, int], _merge_retry_counts]

    # Set by any node that escalates or halts the pipeline
    halt: dict[str, Any] | None

    # Research fan-out accumulator (1.A1.5). Subagents write
    # `{scope: ResearchPart(...)}`; the reducer dict-merges by scope so
    # parallel branches and judge-driven reruns coexist cleanly.
    research_parts: Annotated[dict[str, ResearchPart], _merge_research_parts]

    # Citation source_ids retrieved by every tool call across the run
    # (Sprint 3 3.B.5). Dict-keyed by scope/phase so a judge-driven
    # rerun of a single scope overwrites only that scope's source_ids
    # without orphaning stale entries the validator would otherwise
    # accept. Research_join flattens dict.values() before passing to
    # the validator's set-membership check.
    retrieved_source_ids: Annotated[dict[str, list[str]], _merge_source_ids]

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
