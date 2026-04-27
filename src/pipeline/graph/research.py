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
from src.shared.case_state import CaseState, EvidenceAnalysis, ExtractedFacts, Witnesses

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

    Targeting (Phase 2 — gate-2 scoped rerun): if
    ``state["extra_instructions"]`` carries any research-scope key
    (``evidence`` / ``facts`` / ``witnesses`` / ``law``), fan out only
    to those scopes. The dict-keyed ``research_parts`` accumulator keeps
    the other three scopes' outputs intact — a no-instructions rerun of
    a single subagent therefore preserves its peers' work, not
    overwrites it. Falls through to all-four when ``extra_instructions``
    is empty or only carries non-scope keys (e.g. ``{"gate2": ...}`` —
    the legacy 'whole gate' rerun shape).

    Send payload includes only what the subagent needs to do its work:
    the case state and any judge-supplied extra instructions. Each
    subagent runs independently; LangGraph awaits all dispatched
    branches before transitioning to ``research_join``.
    """
    extra: dict[str, Any] = state.get("extra_instructions") or {}
    target_scopes = [scope for scope in RESEARCH_SCOPES if scope in extra]
    scopes = tuple(target_scopes) if target_scopes else RESEARCH_SCOPES
    payload: dict[str, Any] = {
        "case": state["case"],
        "extra_instructions": extra,
    }
    return [Send(RESEARCH_SUBAGENT_NODES[scope], payload) for scope in scopes]


def research_join_node(state: dict[str, Any]) -> dict[str, Any]:
    """Barrier-fold: merge accumulated `research_parts` into a `ResearchOutput`,
    then enforce citation provenance on the law part (Sprint 3 3.B.5).

    `from_parts` sets `partial=True` when any of the four expected scopes
    is missing from the dict, which the gate2 UI surfaces to the judge.
    Citations whose `supporting_sources` don't match the run's retrieved
    set are stripped and recorded in `LawResearch.suppressed_citations`
    before the join's output reaches the gate.

    The merged ResearchOutput also lands on `case.evidence_analysis` /
    `case.extracted_facts` / `case.witnesses` / `case.legal_rules` etc.
    Without this, persistence reads `None` from the case fields and the
    Gate 2 panel surfaces zero counts even though the agents produced
    real data — `persist_case_results` reads from `state.case`, not from
    the `research_output` slot.
    """
    parts: dict[str, ResearchPart] = state.get("research_parts") or {}
    merged = ResearchOutput.from_parts(parts)
    if merged.law is not None:
        # Dict-by-scope reducer keeps source_ids partitioned per scope; the
        # validator only needs membership across the whole run, so flatten.
        retrieved_by_scope = state.get("retrieved_source_ids") or {}
        retrieved = [src for sources in retrieved_by_scope.values() for src in sources]
        validated_law = validate_law_citations(merged.law, retrieved)
        merged = merged.model_copy(update={"law": validated_law})
    elif "law" in parts:
        # Law subagent ran but produced no LawResearch payload — surface
        # this as `partial` so the gate2 UI can flag the gap. `from_parts`
        # only flips `partial` when a scope key is missing from the dict;
        # a present-but-empty payload would otherwise slip through.
        merged = merged.model_copy(update={"partial": True})

    # Mirror research_output onto the canonical case fields the persistence
    # layer + REST endpoints read from. The _merge_case reducer treats
    # None/[]/{} as "unset" so this is parallel-safe; subagents don't
    # touch these fields, only the join does.
    case_updates: dict[str, Any] = {}
    if merged.evidence is not None:
        case_updates["evidence_analysis"] = EvidenceAnalysis(
            evidence_items=[
                e.model_dump() if hasattr(e, "model_dump") else e
                for e in merged.evidence.evidence_items
            ],
            credibility_scores=dict(merged.evidence.credibility_scores or {}),
        )
    if merged.facts is not None:
        case_updates["extracted_facts"] = ExtractedFacts(
            facts=[f.model_dump() if hasattr(f, "model_dump") else f for f in merged.facts.facts],
            timeline=[
                t.model_dump() if hasattr(t, "model_dump") else t
                for t in (merged.facts.timeline or [])
            ],
        )
    if merged.witnesses is not None:
        case_updates["witnesses"] = Witnesses(
            witnesses=[
                w.model_dump() if hasattr(w, "model_dump") else w
                for w in merged.witnesses.witnesses
            ],
            credibility=dict(merged.witnesses.credibility or {}),
        )
    if merged.law is not None:
        case_updates["legal_rules"] = [
            r.model_dump() if hasattr(r, "model_dump") else r for r in merged.law.legal_rules
        ]
        case_updates["precedents"] = [
            p.model_dump() if hasattr(p, "model_dump") else p for p in merged.law.precedents
        ]
        if merged.law.precedent_source_metadata is not None:
            case_updates["precedent_source_metadata"] = (
                merged.law.precedent_source_metadata.model_dump()
                if hasattr(merged.law.precedent_source_metadata, "model_dump")
                else merged.law.precedent_source_metadata
            )
        case_updates["legal_elements_checklist"] = [
            e.model_dump() if hasattr(e, "model_dump") else e
            for e in merged.law.legal_elements_checklist
        ]
        case_updates["suppressed_citations"] = [
            s.model_dump() if hasattr(s, "model_dump") else s
            for s in merged.law.suppressed_citations
        ]

    update: dict[str, Any] = {"research_output": merged}
    if case_updates:
        existing_case: CaseState = state["case"]
        update["case"] = existing_case.model_copy(update=case_updates)
    return update


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
        # can validate self-reported supporting_sources. Dict-keyed by
        # scope so a judge-driven /rerun of this scope alone overwrites
        # this slot without leaking stale source_ids forward.
        source_ids = result.get("retrieved_source_ids") or []
        if source_ids:
            update["retrieved_source_ids"] = {scope: list(source_ids)}
        return update

    _node.__name__ = f"research_{scope}_node"
    return _node
