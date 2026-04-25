"""Research fan-out wiring (Sprint 1 1.A1.5).

Three pieces implement the canonical LangGraph parallel-research pattern:

1. `research_dispatch_node` — plain pass-through node (no LLM call). Acts
   as the topology entry-point so `add_conditional_edges` has a source.
2. `route_to_research_subagents` — conditional-edge router. Returns one
   `Send(...)` per scope, fanning out the four research subagents.
3. `research_join_node` — barrier-fold. Reads the dict-keyed
   `research_parts` accumulator and merges via
   `ResearchOutput.from_parts(...)`, which sets `partial=True` when any
   expected scope is missing.

`make_research_node(scope)` wraps the bare `make_research_subagent(scope)`
factory so each subagent emits `{"research_parts": {scope: ResearchPart(...)}}`,
the shape consumed by the `_merge_research_parts` reducer on `GraphState`.

The Send-via-conditional-edge pattern (V-4) is mandatory: a node returning
`list[Send]` from its body is NOT supported. Dispatch is a regular node;
the router is wired through `g.add_conditional_edges("research_dispatch",
route_to_research_subagents, [...destinations])`.

Re-entry safety (SA F-2 option 2): `research_parts` is dict-keyed, so a
re-run of a single scope overwrites that key and leaves the other three
intact — no sentinel reset reducer needed. Whole-state resets (e.g. a
judge-driven rerun from gate1) flow through `graph.update_state(...,
Overwrite(...))` from the rerun handler.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.types import Send

from src.pipeline.graph.agents.factory import make_research_subagent
from src.pipeline.graph.output_validator import validate_law_citations
from src.pipeline.graph.schemas import ResearchOutput, ResearchPart

RESEARCH_SCOPES: tuple[str, ...] = ("evidence", "facts", "witnesses", "law")

RESEARCH_SUBAGENT_NODES: dict[str, str] = {scope: f"research_{scope}" for scope in RESEARCH_SCOPES}


def research_dispatch_node(state: dict[str, Any]) -> dict[str, Any]:
    """Plain dispatch node — no state mutation.

    The accumulator is dict-keyed (`_merge_research_parts`), so re-running
    a subagent naturally overwrites its scope. Whole-pipeline resets are
    handled out-of-band via `graph.update_state(..., Overwrite([]))` in
    the rerun handler, NOT here.
    """
    return {}


def route_to_research_subagents(state: dict[str, Any]) -> list[Send]:
    """Conditional-edge router: fan out one `Send` per research scope.

    Send payload includes only what the subagent needs to do its work:
    the case state and any judge-supplied extra instructions. Each
    subagent runs independently; LangGraph awaits all four before
    transitioning to `research_join`.
    """
    payload: dict[str, Any] = {
        "case": state["case"],
        "extra_instructions": state.get("extra_instructions", {}),
    }
    return [Send(RESEARCH_SUBAGENT_NODES[scope], payload) for scope in RESEARCH_SCOPES]


def research_join_node(state: dict[str, Any]) -> dict[str, Any]:
    """Barrier-fold: merge accumulated `research_parts` into a `ResearchOutput`,
    then enforce citation provenance on the law part (Sprint 3 3.B.5).

    `from_parts` sets `partial=True` when any of the four expected scopes
    is missing from the dict, which the gate2 UI surfaces to the judge.
    Citations whose `supporting_sources` don't match the run's retrieved
    set are stripped and recorded in `LawResearch.suppressed_citations`
    before the join's output reaches the gate.
    """
    parts: dict[str, ResearchPart] = state.get("research_parts") or {}
    merged = ResearchOutput.from_parts(parts)
    if merged.law is not None:
        retrieved = state.get("retrieved_source_ids") or []
        validated_law = validate_law_citations(merged.law, retrieved)
        merged = merged.model_copy(update={"law": validated_law})
    return {"research_output": merged}


def make_research_node(scope: str) -> Callable:
    """Build a research-subagent node that emits the dict-keyed accumulator shape.

    Wraps `make_research_subagent(scope)` from the 1.A1.4 factory. The
    factory's bare node returns `{f"research-{scope}_output": <schema>}`,
    which is the right shape for a single-phase consumer but the wrong
    shape for the parallel reducer. This wrapper repackages the
    structured output as `{"research_parts": {scope: ResearchPart(...)}}`
    so `_merge_research_parts` can fold across the four parallel branches.
    """
    if scope not in RESEARCH_SCOPES:
        raise ValueError(f"Unknown research scope: {scope!r}; expected one of {RESEARCH_SCOPES}")

    inner = make_research_subagent(scope)
    structured_key = f"research-{scope}_output"

    async def _node(state: dict[str, Any]) -> dict[str, Any]:
        result = await inner(state)
        structured = result.get(structured_key)
        part = ResearchPart(scope=scope, **{scope: structured})
        update: dict[str, Any] = {"research_parts": {scope: part}}
        # Sprint 3 3.B.5 — fan citation source_ids up to the join so it
        # can validate self-reported supporting_sources.
        source_ids = result.get("retrieved_source_ids") or []
        if source_ids:
            update["retrieved_source_ids"] = list(source_ids)
        return update

    _node.__name__ = f"research_{scope}_node"
    return _node
