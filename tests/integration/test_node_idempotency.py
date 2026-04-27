"""Sprint 4 4.A3.2 — gate-node idempotency contract.

Locks the verdict from `tasks/gate-node-idempotency-audit.md` (4.A3.1):
the current `make_gate_pause` and `make_gate_apply` factories perform
zero non-idempotent work before `interrupt()` returns.

`langgraph.types.interrupt()` re-runs the node from the top each time
the judge resumes. Any DB INSERT, external API call, or counter
mutation that runs *before* `interrupt()` will fire on every resume,
producing duplicates. The audit found nothing to fix in the minimal
Sprint-1 implementation; this test makes the invariant load-bearing
so a future change cannot silently regress it.

Two contracts are checked:

1. ``make_gate_pause`` — repeated invocation produces an identical
   ``interrupt()`` payload and no other observable effect (we patch
   ``interrupt`` to capture the value and raise ``GraphInterrupt``,
   matching the real langgraph pre-resume behaviour).
2. ``make_gate_apply`` — pure function: same input ``state`` →
   ``Command`` with identical ``goto`` and ``update``, no module-level
   bookkeeping.
"""

from __future__ import annotations

from unittest import mock

import pytest
from langgraph.errors import GraphInterrupt
from langgraph.types import Interrupt

from src.pipeline.graph.nodes.gates import (
    GATE_NAMES,
    make_gate_apply,
    make_gate_pause,
)
from src.shared.case_state import CaseState


def _state(*, pending_action=None) -> dict:
    return {
        "case": CaseState(case_id="00000000-0000-0000-0000-000000000abc"),
        "pending_action": pending_action,
    }


@pytest.mark.parametrize("gate", GATE_NAMES)
def test_pause_node_has_no_pre_interrupt_side_effects(gate: str) -> None:
    """Repeated entry into the pause node fires identical interrupt payloads.

    Anything written *before* ``interrupt()`` would multiply on each
    resume — checkpointer replay re-executes the node from the top.
    """
    pause = make_gate_pause(gate)
    captured: list[dict] = []

    def fake_interrupt(value):
        captured.append(value)
        raise GraphInterrupt((Interrupt(value=value, id=f"{gate}-test"),))

    with mock.patch(
        "src.pipeline.graph.nodes.gates.interrupt",
        side_effect=fake_interrupt,
    ):
        state = _state()
        for _ in range(3):
            with pytest.raises(GraphInterrupt):
                pause(state)

    assert len(captured) == 3, "interrupt() must fire on every entry"
    assert all(payload == captured[0] for payload in captured), (
        "interrupt payload must be identical on each replay; otherwise "
        "the checkpointer cannot deterministically resume the node"
    )
    # Minimum payload keys present (4.A3.3 will add phase_output, trace_id,
    # etc.; do not pin the upper bound). The replay-equality check above is
    # the real idempotency contract.
    payload = captured[0]
    required = {"gate", "case_id", "actions"}
    assert required.issubset(payload.keys()), (
        f"pause payload missing required keys; got {sorted(payload.keys())}"
    )


@pytest.mark.parametrize("gate", GATE_NAMES)
@pytest.mark.parametrize(
    "pending_action",
    [
        {"action": "advance"},
        {"action": "rerun", "notes": "tighten precedent search"},
        {"action": "halt"},
        "advance",
        None,
    ],
)
def test_apply_node_is_pure(gate: str, pending_action) -> None:
    """``make_gate_apply`` must be pure — same input → same Command.

    Two back-to-back invocations on the same state must yield Commands
    with identical ``goto`` and ``update``. No DB I/O, no external
    calls, no module-level counters.
    """
    apply_node = make_gate_apply(
        gate,
        advance_target=f"{gate}_advance",
        rerun_target=f"{gate}_rerun",
    )

    state = _state(pending_action=pending_action)

    first = apply_node(state)
    second = apply_node(state)

    assert first.goto == second.goto, (
        f"apply.goto must be deterministic; got {first.goto!r} then {second.goto!r}"
    )
    assert first.update == second.update, (
        f"apply.update must be deterministic; got {first.update!r} then {second.update!r}"
    )
