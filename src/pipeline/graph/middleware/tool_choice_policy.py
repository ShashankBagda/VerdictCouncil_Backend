"""Per-phase `tool_choice` policy.

Synthesis is the v1 surface for the `ask_judge` chat-steering tool. The
prompt mandates at least one call per run so every gate-3 review carries
a substantive Judge-only question, but a soft mandate in prose is not
enforcement — the model can (and does) skip the tool, which leaves the
chat panel idle and silently downgrades gate-3 to a pure approval gate.

This middleware enforces the call: on the synthesis phase's first
model turn, if no `ask_judge` tool call has been made yet on this run,
override `tool_choice` to `"ask_judge"` so the OpenAI Responses API is
forced to invoke the tool. After the first call, the override clears
and the model is free to issue follow-up reasoning, additional tool
calls, or the structured-response binding.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import wrap_model_call
from langchain_core.messages import AIMessage


def _state_field(state: Any, name: str) -> str:
    if isinstance(state, dict):
        return str(state.get(name, ""))
    return str(getattr(state, name, ""))


def _ask_judge_already_called(messages: list[Any]) -> bool:
    for msg in messages:
        if isinstance(msg, AIMessage):
            for tc in msg.tool_calls or []:
                if tc.get("name") == "ask_judge":
                    return True
    return False


@wrap_model_call
async def enforce_synthesis_ask_judge(request, handler):  # noqa: ANN001
    """Force `tool_choice="ask_judge"` on the synthesis phase's first
    turn. No-op for every other phase, and no-op once `ask_judge` has
    been called on this run.
    """
    agent_name = _state_field(request.state, "agent_name")
    if agent_name == "synthesis" and not _ask_judge_already_called(request.messages):
        # Only force when the tool is actually available — defensive
        # against future tool-set changes that drop ask_judge from
        # synthesis. Falling back to whatever the caller passed (which
        # is None today) keeps the agent runnable instead of erroring
        # out when the policy can't apply.
        tool_names = {getattr(t, "name", None) for t in (request.tools or [])}
        if "ask_judge" in tool_names:
            request = request.override(tool_choice="ask_judge")
    return await handler(request)
