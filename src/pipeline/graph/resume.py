"""Sprint 4 4.A3.5 / 4.A3.6 — drive `Command(resume=...)` from a job payload.

Centralises the worker-side translation of a ``/respond``-shaped payload
into a LangGraph ``Command(resume=...)`` invocation. Keeping this in its
own module lets the worker stay thin and lets the unit tests target the
contract directly without a real Redis / Postgres stack.

The worker entry-point (`workers.tasks.run_gate_job`) calls
:func:`drive_resume` after re-establishing OTEL trace context. The
function:

1. Verifies the saver has a pending interrupt at this thread_id — that
   is the only legitimate state in which a resume can land.
2. Translates the job payload into the gate-apply resume dict via
   :func:`build_resume_payload`. Subagent-targeted notes ride as a
   ``{subagent: note}`` dict so ``make_gate_apply`` writes them
   straight into ``extra_instructions`` scoped to the targeted scope.
3. Invokes the graph with the ``Command(resume=...)`` payload the
   :func:`make_gate_apply` node consumes.
4. Inspects the post-invoke state and returns one of two outcomes —
   ``("interrupt", gate, payload)`` if the run paused at another gate,
   or ``("terminal", None, None)`` if the run reached END.

The caller is responsible for persisting case results and publishing
the InterruptEvent — both are side-effecting and want a real DB
session, which the worker provides.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

logger = logging.getLogger(__name__)


_PAUSE_NODE_PREFIX = "gate"
_PAUSE_NODE_SUFFIX = "_pause"


def gate_from_pause_node(node_name: str) -> str | None:
    """Return ``gate1`` … ``gate4`` from a ``gate{N}_pause`` node name."""
    if not node_name.startswith(_PAUSE_NODE_PREFIX) or not node_name.endswith(_PAUSE_NODE_SUFFIX):
        return None
    middle = node_name[len(_PAUSE_NODE_PREFIX) : -len(_PAUSE_NODE_SUFFIX)]
    if not middle.isdigit():
        return None
    return f"gate{middle}"


def build_resume_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate a /respond job payload into the gate-apply resume dict.

    Only the keys the apply node consumes are forwarded:

    - ``action`` (required)
    - ``notes`` (optional — folded into extra_instructions on rerun)
    - ``field_corrections`` (optional — atomic GraphState slot writes)

    Subagent targeting (gate2 research scope) is encoded by handing
    ``notes`` to the apply node as a ``{subagent: note}`` dict rather
    than a bare string. ``make_gate_apply`` writes that dict straight
    into ``extra_instructions``, so the targeted research subagent
    picks the corrective note up via its standard
    ``state["extra_instructions"][scope]`` lookup on the next fan-out.
    """
    action = payload.get("resume_action")
    if action not in {"advance", "rerun", "halt"}:
        raise ValueError(f"resume_action must be one of advance/rerun/halt; got {action!r}")

    resume: dict[str, Any] = {"action": action}
    notes = payload.get("notes")
    subagent = payload.get("subagent")
    if action == "rerun" and subagent and notes:
        # Targeted note — keyed by subagent so the dispatcher's Send
        # payload reaches the right scope. Other scopes still re-run
        # but without a corrective instruction.
        resume["notes"] = {str(subagent): str(notes)}
    elif notes:
        resume["notes"] = notes
    if isinstance(payload.get("field_corrections"), dict):
        resume["field_corrections"] = payload["field_corrections"]
    return resume


async def has_pending_interrupt(graph: CompiledStateGraph[Any], config: dict[str, Any]) -> bool:
    """True when the saver has a checkpointed task awaiting `Command(resume=...)`."""
    state = await graph.aget_state(cast(RunnableConfig, config))
    return any(t.interrupts for t in state.tasks)


async def find_pending_interrupt(
    graph: CompiledStateGraph[Any], config: dict[str, Any]
) -> tuple[str, dict[str, Any]] | None:
    """Return ``(gate, interrupt_payload)`` for the first pending pause task.

    None when the saver has no pending interrupt — the caller treats
    that as the run-reached-END case.
    """
    state = await graph.aget_state(cast(RunnableConfig, config))
    for task in state.tasks:
        if not task.interrupts:
            continue
        gate = gate_from_pause_node(task.name)
        if gate is None:
            # Defensive: an interrupt fired from a non-gate node. We still
            # want to surface it so the caller can react, but with an
            # empty gate label so the downstream UPSERT skips.
            logger.warning(
                "interrupt fired from non-gate node %r — skipping legacy compat",
                task.name,
            )
            continue
        first = task.interrupts[0]
        value = getattr(first, "value", None)
        if not isinstance(value, dict):
            value = {}
        return gate, value
    return None


ResumeOutcome = Literal["interrupt", "terminal"]


#: Phase → the gate that pauses *after* it. The send-back mechanic
#: rewinds the thread to that gate's interrupted checkpoint and issues
#: a ``Command(resume={"action": "rerun"})`` so the gate-apply node
#: re-runs the target phase. We can't fork at the phase entry node
#: directly because LangGraph replays resolved interrupts on the new
#: branch, blowing past the gate's pause without firing.
_PHASE_FOLLOWING_GATE: dict[str, str] = {
    "intake": "gate1",
    "research": "gate2",
    "synthesis": "gate3",
}


async def send_back_to_phase(
    graph: CompiledStateGraph[Any],
    config: dict[str, Any],
    *,
    to_phase: str,
    notes: str | None = None,
) -> str | None:
    """Rewind the LangGraph thread to a past phase's gate pause and rerun.

    Sprint 4 4.A3.14 — the auditor `recommend_send_back` mechanic.

    Walks ``aget_state_history`` newest-first to find the most recent
    interrupted checkpoint at the gate that pauses immediately after
    ``to_phase`` (e.g. ``gate3`` for ``to_phase="synthesis"``). Forks
    from there via ``aupdate_state(past_config, ...)`` to inject
    ``extra_instructions[target_phase] = notes``, then invokes
    ``Command(resume={"action": "rerun"})`` so the gate-apply node
    routes to the rerun target — which by topology is the target phase
    itself. The phase re-runs with the new instructions and the gate
    pauses fresh on the new branch.

    Why this path rather than forking at the phase entry: LangGraph
    replays previously-resolved interrupts on a fork, so forking at
    e.g. ``next=('synthesis',)`` runs synthesis but blows through
    gate3_pause's already-resolved interrupt to gate4 / END without
    pausing. Forking at the gate-pause checkpoint and sending a fresh
    ``Command(resume=...)`` makes the gate's interrupt fire again
    naturally.

    Later (post-rewind, pre-fork) checkpoints stay accessible via
    ``aget_state_history`` for the audit trail — the rewind extends
    history rather than truncating it.

    Returns the gate name where the rewound run paused (e.g. ``"gate3"``
    after a ``send_back`` to synthesis), or ``None`` if no interrupt is
    pending after the rerun.

    Raises:
        ValueError: ``to_phase`` is not in ``{intake, research,
            synthesis}``. ``audit`` is excluded — sending back to audit
            is a rerun-audit, not a rewind; use ``should_rerun=True`` +
            ``target_phase="audit"`` for that.
        RuntimeError: no checkpoint in the thread's history has the
            following gate's pause interrupted. Means the thread either
            never reached the target phase's gate or has no history
            (programming error — fail loudly rather than silently).
    """
    if to_phase not in _PHASE_FOLLOWING_GATE:
        raise ValueError(
            f"send_back: to_phase must be one of {sorted(_PHASE_FOLLOWING_GATE)}; "
            f"got {to_phase!r} (audit is excluded — use rerun for that)"
        )

    target_gate = _PHASE_FOLLOWING_GATE[to_phase]
    target_pause_node = f"{target_gate}_pause"
    runnable_config = cast(RunnableConfig, config)

    # Newest-first walk; the first matching pause-checkpoint is the
    # most recent execution of the gate, which is what we want as the
    # rewind point.
    target_config: RunnableConfig | None = None
    async for snap in graph.aget_state_history(runnable_config):
        if any(
            task.name == target_pause_node and task.interrupts
            for task in snap.tasks
        ):
            target_config = snap.config
            break

    if target_config is None:
        raise RuntimeError(
            f"send_back: no interrupted checkpoint at {target_pause_node!r} in "
            f"thread {config.get('configurable', {}).get('thread_id')!r} — "
            f"the thread either never reached phase {to_phase!r} or has no "
            f"history."
        )

    # Fork: write extra_instructions on the past checkpoint. update_state
    # returns a new config pointing at the fork's head.
    update_payload: dict[str, Any] = {}
    if notes:
        update_payload["extra_instructions"] = {to_phase: notes}
    if update_payload:
        forked_config = await graph.aupdate_state(target_config, update_payload)
    else:
        forked_config = target_config

    # Resume the forked checkpoint with action=rerun. The gate's apply
    # node routes to its rerun_target (the target phase). The phase
    # re-runs and the gate pauses fresh on the new branch.
    await graph.ainvoke(Command(resume={"action": "rerun"}), forked_config)

    pending = await find_pending_interrupt(graph, config)
    return pending[0] if pending is not None else None


async def cancel_via_halt(
    graph: CompiledStateGraph[Any],
    config: dict[str, Any],
    *,
    reason: str | None = None,
    by: str | None = None,
) -> None:
    """Cancel a run via the saver-halt path (Sprint 4 4.A3.9).

    Two cases the helper covers:

    - **Paused at a gate** — drives ``Command(resume={"action": "halt"})``
      so the gate-apply node populates the `halt` slot and routes to
      `terminal` in one super-step.
    - **Mid-execution in a worker** — no pending interrupt is available,
      so the helper writes the `halt` slot directly via
      :meth:`aupdate_state`. The cancellation middleware reads `state.halt`
      on the next super-step boundary and short-circuits the agent loop
      with `Command(goto="end")`.

    Either way the durable signal lives in the saver, not in Redis. The
    legacy Redis cancel-flag (`set_cancel_flag` / `check_cancel_flag`)
    remains in `services/pipeline_events.py` for the legacy
    `_run_case_pipeline` run-end status detection only — the agent loop
    no longer consults it (4.A3.9 acceptance: "Redis cancel-flag code
    path retired or neutralized").

    The halt payload always uses ``reason="cancelled"`` so downstream
    consumers can distinguish a judge cancellation from a judge halt
    (which uses ``reason="judge_halt"`` in the gate-apply node).
    """
    halt_payload: dict[str, Any] = {"reason": "cancelled"}
    if by:
        halt_payload["by"] = by
    if reason:
        halt_payload["notes"] = reason

    if await has_pending_interrupt(graph, config):
        resume_payload: dict[str, Any] = {"action": "halt"}
        if reason:
            resume_payload["notes"] = reason
        await graph.ainvoke(
            Command(resume=resume_payload), cast(RunnableConfig, config)
        )
        # The gate-apply node sets halt.reason="judge_halt"; overwrite to
        # "cancelled" + carry the `by` field, which judge_halt does not.
        await graph.aupdate_state(
            cast(RunnableConfig, config), {"halt": halt_payload}
        )
        return

    await graph.aupdate_state(cast(RunnableConfig, config), {"halt": halt_payload})


async def drive_resume(
    graph: CompiledStateGraph[Any],
    config: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[ResumeOutcome, str | None, dict[str, Any] | None]:
    """Translate a /respond job payload into a saver-driven resume.

    Returns one of:

    - ``("interrupt", gate, interrupt_payload)`` — the run paused at the
      next gate. Caller publishes ``InterruptEvent`` and UPSERTs the
      legacy ``awaiting_review_gateN`` status.
    - ``("terminal", None, None)`` — the run reached END (gate4 advance,
      gate halt, or final phase completion). Caller persists terminal
      case state and emits the legacy ``terminal`` progress event.

    Raises ``RuntimeError`` if the saver has no pending interrupt at
    this thread_id — that is a sign of a payload/state mismatch
    (e.g. a stale outbox job for a thread that already advanced) and
    the worker should fail loudly rather than silently re-running.
    """
    if not await has_pending_interrupt(graph, config):
        raise RuntimeError(
            "drive_resume invoked but the saver has no pending interrupt at "
            f"thread_id={config.get('configurable', {}).get('thread_id')!r}. "
            "Refusing to silently re-execute the graph from START."
        )

    resume = build_resume_payload(payload)
    await graph.ainvoke(Command(resume=resume), cast(RunnableConfig, config))

    pending = await find_pending_interrupt(graph, config)
    if pending is None:
        return "terminal", None, None
    gate, interrupt_payload = pending
    return "interrupt", gate, interrupt_payload
