"""Cancellation middleware — saver-halt short-circuit (Sprint 4 4.A3.9).

`cancel_check` is a `@before_model` hook. It reads the `halt` slot from
GraphState; when populated, the agent loop returns `Command(goto="end")`
to skip the next model call.

The cancel signal flows from `POST /cases/{id}/cancel`, which calls
`graph.aupdate_state(config, {"halt": {...}})` on the saver. Between
super-steps the worker reloads state from the saver, so the middleware
sees the halt slot at the next node boundary — within ≤1 super-step.

Sprint 1 1.A1.2 originally read this signal from a Redis cancel-flag.
4.A3.9 retires that path: cancellation is now a property of saver state,
which keeps it survivable across worker restarts and visible to LangSmith
trace inspection without an out-of-band Redis lookup.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import before_model
from langgraph.types import Command


def _state_halt(state: Any) -> Any:
    if isinstance(state, dict):
        return state.get("halt")
    return getattr(state, "halt", None)


@before_model(can_jump_to=["end"])
async def cancel_check(state, runtime):  # noqa: ANN001
    """Return `Command(goto="end")` when the `halt` slot is populated."""
    if _state_halt(state):
        return Command(goto="end")
    return None
