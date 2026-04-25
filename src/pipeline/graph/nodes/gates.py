"""Minimal HITL gate factories (Sprint 1 1.A1.7).

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
  with optional `extra_instructions`), and `halt` (terminate the run).

Sprint 1 lands the contract: `interrupt()` fires, `Command(resume=...)`
resumes correctly. Full payloads (snapshot-for-judge, idempotent status
upserts, frontend-rendered review surfaces) are explicit Sprint 4 / 4.A3
work and are intentionally NOT included here. The breakdown's reference
code is prescriptive, not descriptive — `snapshot_for_gate(...)` and
`upsert_case_status(...)` were never implemented and would block this
PR if treated as prerequisites.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.graph import END
from langgraph.types import Command, interrupt

GATE_NAMES: tuple[str, ...] = ("gate1", "gate2", "gate3", "gate4")

GATE_ACTIONS: tuple[str, ...] = ("advance", "rerun", "halt")


def make_gate_pause(gate: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build a gate-pause node that fires `interrupt()` for judge review.

    The interrupt payload is intentionally minimal in Sprint 1: gate name,
    case id, available actions. Sprint 4 (4.A3) extends this with the
    full review surface (phase outputs, narration, LangSmith trace links).
    """
    if gate not in GATE_NAMES:
        raise ValueError(f"Unknown gate: {gate!r}; expected one of {GATE_NAMES}")

    def _pause(state: dict[str, Any]) -> dict[str, Any]:
        case = state["case"]
        decision = interrupt(
            {
                "gate": gate,
                "case_id": str(case.case_id),
                "actions": list(GATE_ACTIONS),
            }
        )
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
    or a bare action string. Anything unrecognised falls through to
    `advance` so a malformed judge response never silently halts a run.
    """
    if gate not in GATE_NAMES:
        raise ValueError(f"Unknown gate: {gate!r}; expected one of {GATE_NAMES}")

    def _apply(state: dict[str, Any]) -> Command:
        raw = state.get("pending_action")
        if isinstance(raw, dict):
            action = raw.get("action", "advance")
            notes = raw.get("notes")
        else:
            action = raw if isinstance(raw, str) else "advance"
            notes = None

        if action == "rerun":
            updates: dict[str, Any] = {"pending_action": None}
            if notes:
                # Merge into extra_instructions; LWW reducer overwrites prior
                # entries for the same agent name.
                updates["extra_instructions"] = (
                    notes if isinstance(notes, dict) else {gate: str(notes)}
                )
            return Command(update=updates, goto=rerun_target)

        if action == "halt":
            return Command(
                update={"pending_action": None, "halt": {"reason": "judge_halt", "gate": gate}},
                goto="terminal",
            )

        # Default + explicit "advance"
        return Command(update={"pending_action": None}, goto=advance_target)

    _apply.__name__ = f"{gate}_apply_node"
    return _apply


__all__ = ["GATE_NAMES", "GATE_ACTIONS", "make_gate_pause", "make_gate_apply", "END"]
