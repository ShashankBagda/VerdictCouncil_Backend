"""Cancellation middleware: short-circuits agent runs when the judge cancels.

`cancel_check` is a `@before_model` hook. It consults the Redis cancel flag
that `POST /cases/{id}/cancel` sets, and if the flag is up it returns
`Command(goto="end")` to skip the model call. Mirrors the in-loop check in
`nodes/common.py:_run_agent_node` (the line that calls
`check_cancel_flag(case_id)` before each turn).
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import before_model
from langgraph.types import Command

from src.services.pipeline_events import check_cancel_flag


def _state_field(state: Any, name: str) -> str:
    if isinstance(state, dict):
        return str(state.get(name, ""))
    return str(getattr(state, name, ""))


@before_model(can_jump_to=["end"])
async def cancel_check(state, runtime):  # noqa: ANN001
    """Return `Command(goto="end")` when the judge has cancelled the case."""
    case_id = _state_field(state, "case_id")
    if not case_id:
        return None
    if await check_cancel_flag(case_id):
        return Command(goto="end")
    return None
