"""HITL gate factories — pause/apply pairs (Sprint 1 1.A1.7 + Sprint 4 4.A3).

Each gate is implemented as a `pause` + `apply` pair:

- `make_gate_pause(gate)` is a regular node that calls `interrupt(payload)`
  and stores the judge's reply in `pending_action`. LangGraph's
  checkpointer persists state at the interrupt boundary; the next call
  to `compiled.ainvoke(Command(resume=...), config)` returns the value
  from `interrupt(...)` and the node continues.
- `make_gate_apply(gate, advance_target, rerun_target)` reads
  `pending_action`, dispatches to the right successor via
  `Command(goto=...)`, and clears the slot. The three actions are
  `advance` (continue forward), `rerun` (re-execute the previous phase
  with optional `extra_instructions` and inline `field_corrections`),
  and `halt` (terminate the run via `terminal`). A fourth action
  (`send_back`) exists in the unified `/cases/{id}/respond` contract
  (4.A3.15) but is short-circuited at the API layer — it rewinds the
  thread to a past phase checkpoint and re-invokes; it never reaches
  the apply node.

Sprint 4 4.A3.3 enriches the interrupt payload with the per-gate phase
output snapshot, the active OTEL trace id, and (for gate4) the audit
summary including any `recommend_send_back` recommendation. The
extraction is keyed off `GATE_PHASE_SLOT` so callers don't have to
know the gate→phase mapping.

The full review surface — DB writes for legacy `awaiting_review_gateN`
status, frontend `<GateReviewPanel>` mounting, side-by-side compare
for What-If — lives outside this module: 4.A3.7 emits the
``InterruptEvent`` and writes the legacy compat fields, 4.C5b mounts
the panels, 4.A3.15 routes judge actions through `/respond`. Keeping
gate nodes side-effect-free preserves the 4.A3.2 idempotency contract.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from langgraph.graph import END
from langgraph.types import Command, interrupt

logger = logging.getLogger(__name__)

GATE_NAMES: tuple[str, ...] = ("gate1", "gate2", "gate3", "gate4")

GATE_ACTIONS: tuple[str, ...] = ("advance", "rerun", "halt")

#: GraphState slot for the phase output rendered to the judge at each gate.
GATE_PHASE_SLOT: dict[str, str] = {
    "gate1": "intake_output",
    "gate2": "research_output",
    "gate3": "synthesis_output",
    "gate4": "audit_output",
}


def _current_trace_id() -> str | None:
    """Return the active W3C OTEL trace id (32 lowercase hex), or None.

    Imported lazily so the gate factories stay importable in unit tests
    that don't initialise the OTEL provider (Sprint 2 2.C1.x already
    wires the provider in production paths).
    """
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context() if span else None
        if ctx is None or not ctx.is_valid:
            return None
        return f"{ctx.trace_id:032x}"
    except Exception:  # pragma: no cover - defensive
        return None


def _serialize_phase_output(value: Any) -> Any:
    """Coerce a phase output (Pydantic model / dict / None) to JSON-able dict.

    Returning ``None`` means the slot was unset — we omit the field from
    the interrupt payload rather than emit ``"phase_output": null`` so
    consumers can distinguish "no payload" from "payload contained nulls".
    """
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    return None  # unsupported shape — skip rather than crash


def _gate4_audit_summary(audit_output: Any) -> dict[str, Any] | None:
    """Lightweight summary of `AuditOutput` for the gate4 review panel.

    Includes the optional `recommend_send_back` field so the frontend
    (4.C5b.2 Gate4 panel) can surface the auditor's recommendation
    without re-fetching the full audit output.
    """
    if audit_output is None:
        return None
    data = _serialize_phase_output(audit_output)
    if not isinstance(data, dict):
        return None
    summary: dict[str, Any] = {}
    # `recommend_send_back` lands in 4.A3.14; the others are present today.
    for key in (
        "recommend_send_back",
        "fairness_check",
        "should_rerun",
        "target_phase",
        "reason",
    ):
        if key in data:
            summary[key] = data[key]
    return summary or None


def make_gate_pause(gate: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build a gate-pause node that fires `interrupt()` for judge review.

    The payload always carries ``gate``, ``case_id``, and ``actions``.
    When the corresponding phase output slot is populated, ``phase_output``
    is included so the frontend can render the review panel without an
    extra fetch. ``trace_id`` is included when an OTEL span is active
    (Sprint 2 2.C1.5 propagation), letting `<GateReviewPanel>` deep-link
    into LangSmith. Gate4 additionally gets ``audit_summary`` carrying
    any auditor `recommend_send_back` recommendation (4.A3.14).

    All payload fields are deterministic w.r.t. the input state — calling
    the node twice on the same state produces an identical payload, which
    is what 4.A3.2's idempotency test locks in. ``trace_id`` is the one
    field that *can* differ across executions (different request → new
    trace), but it is identical across replays of the *same* request
    because the OTEL context is checkpointed by the worker.
    """
    if gate not in GATE_NAMES:
        raise ValueError(f"Unknown gate: {gate!r}; expected one of {GATE_NAMES}")

    phase_slot = GATE_PHASE_SLOT[gate]

    def _pause(state: dict[str, Any]) -> dict[str, Any]:
        case = state["case"]
        payload: dict[str, Any] = {
            "gate": gate,
            "case_id": str(case.case_id),
            "actions": list(GATE_ACTIONS),
        }

        phase_output = _serialize_phase_output(state.get(phase_slot))
        if phase_output is not None:
            payload["phase_output"] = phase_output

        if gate == "gate4":
            audit_summary = _gate4_audit_summary(state.get("audit_output"))
            if audit_summary is not None:
                payload["audit_summary"] = audit_summary

        trace_id = _current_trace_id()
        if trace_id is not None:
            payload["trace_id"] = trace_id

        decision = interrupt(payload)
        return {"pending_action": decision}

    _pause.__name__ = f"{gate}_pause_node"
    return _pause


def make_gate_apply(
    gate: str,
    *,
    advance_target: str,
    rerun_target: str,
) -> Callable[[dict[str, Any]], Command]:
    """Build a gate-apply node that routes per the judge's decision.

    The resume payload is normalised to a dict (`{"action": ..., ...}`)
    or a bare action string. Optional fields:

    - ``notes`` — freeform judge note. Folded into ``extra_instructions``
      (keyed by gate) on rerun; included on the halt reason.
    - ``field_corrections`` — dict of GraphState slot updates applied
      atomically with the rerun (4.C5b.2 gate3 inline edits).

    Anything unrecognised falls through to ``advance`` so a malformed
    judge response never silently halts a run.
    """
    if gate not in GATE_NAMES:
        raise ValueError(f"Unknown gate: {gate!r}; expected one of {GATE_NAMES}")

    def _apply(state: dict[str, Any]) -> Command:
        raw = state.get("pending_action")
        if isinstance(raw, dict):
            action = raw.get("action", "advance")
            notes = raw.get("notes")
            field_corrections = raw.get("field_corrections")
        else:
            action = raw if isinstance(raw, str) else "advance"
            notes = None
            field_corrections = None

        if action == "rerun":
            updates: dict[str, Any] = {"pending_action": None}
            if notes:
                # Merge into extra_instructions; LWW reducer overwrites prior
                # entries for the same agent name.
                updates["extra_instructions"] = (
                    notes if isinstance(notes, dict) else {gate: str(notes)}
                )
            if isinstance(field_corrections, dict):
                # Inline state corrections (e.g. gate3 judge edits to
                # judicial_questions). Keys must match GraphState slots.
                for slot, value in field_corrections.items():
                    updates[slot] = value
            return Command(update=updates, goto=rerun_target)

        if action == "halt":
            halt_reason: dict[str, Any] = {"reason": "judge_halt", "gate": gate}
            if notes:
                halt_reason["notes"] = str(notes)
            return Command(
                update={"pending_action": None, "halt": halt_reason},
                goto="terminal",
            )

        # Default + explicit "advance"
        return Command(update={"pending_action": None}, goto=advance_target)

    _apply.__name__ = f"{gate}_apply_node"
    return _apply


__all__ = [
    "GATE_ACTIONS",
    "GATE_NAMES",
    "GATE_PHASE_SLOT",
    "END",
    "make_gate_apply",
    "make_gate_pause",
]
