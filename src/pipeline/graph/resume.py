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
2. Optionally side-loads ``extra_instructions`` for a research subagent
   rerun, since the gate apply node only knows how to scope notes to
   the gate's own key. (The dispatcher fans out to all four subagents
   either way; the corrective note rides with the targeted scope only.)
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
from typing import Any, Literal

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

logger = logging.getLogger(__name__)


_PAUSE_NODE_PREFIX = "gate"
_PAUSE_NODE_SUFFIX = "_pause"


def gate_from_pause_node(node_name: str) -> str | None:
    """Return ``gate1`` … ``gate4`` from a ``gate{N}_pause`` node name."""
    if not node_name.startswith(_PAUSE_NODE_PREFIX) or not node_name.endswith(
        _PAUSE_NODE_SUFFIX
    ):
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
        raise ValueError(
            f"resume_action must be one of advance/rerun/halt; got {action!r}"
        )

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


async def has_pending_interrupt(
    graph: CompiledStateGraph, config: dict[str, Any]
) -> bool:
    """True when the saver has a checkpointed task awaiting `Command(resume=...)`."""
    state = await graph.aget_state(config)
    return any(t.interrupts for t in state.tasks)


async def find_pending_interrupt(
    graph: CompiledStateGraph, config: dict[str, Any]
) -> tuple[str, dict[str, Any]] | None:
    """Return ``(gate, interrupt_payload)`` for the first pending pause task.

    None when the saver has no pending interrupt — the caller treats
    that as the run-reached-END case.
    """
    state = await graph.aget_state(config)
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


async def drive_resume(
    graph: CompiledStateGraph,
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
    await graph.ainvoke(Command(resume=resume), config)

    pending = await find_pending_interrupt(graph, config)
    if pending is None:
        return "terminal", None, None
    gate, interrupt_payload = pending
    return "interrupt", gate, interrupt_payload
