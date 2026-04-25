"""SSE bridge middleware: emits tool-call + token-usage events on the wire.

`sse_tool_emitter` (`@wrap_tool_call`) fires `tool_call` before the tool
runs and `tool_result` after, matching the wire shape recorded in
`tests/fixtures/sse_wire_format/agent_tool_*.json`. `token_usage_emitter`
(`@wrap_model_call`) fires once per model turn whenever the AIMessage
carries `usage_metadata`.

Both helpers read `case_id` / `agent_name` from the agent state so the
phase factory in 1.A1.4 doesn't need to thread them through closures.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from langchain.agents.middleware import wrap_model_call, wrap_tool_call
from langchain_core.messages import AIMessage

from src.services.pipeline_events import publish_agent_event


def _state_field(state: Any, name: str) -> str:
    """Read `case_id` / `agent_name` off the (possibly TypedDict / dict / Mapping) state."""
    if isinstance(state, dict):
        return str(state.get(name, ""))
    return str(getattr(state, name, ""))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@wrap_tool_call
async def sse_tool_emitter(request, handler):  # noqa: ANN001
    """Emit `tool_call` before, `tool_result` after — see fixture
    `tests/fixtures/sse_wire_format/scenario_multi_tool_call.json` for
    the canonical sequence.
    """
    case_id = _state_field(request.state, "case_id")
    agent_name = _state_field(request.state, "agent_name")
    tool_call = request.tool_call

    await publish_agent_event(
        case_id,
        {
            "case_id": case_id,
            "agent": agent_name,
            "event": "tool_call",
            "tool_name": tool_call.get("name", ""),
            "args": tool_call.get("args", {}),
            "ts": _now_iso(),
        },
    )

    result = await handler(request)

    await publish_agent_event(
        case_id,
        {
            "case_id": case_id,
            "agent": agent_name,
            "event": "tool_result",
            "tool_name": tool_call.get("name", ""),
            "result": str(getattr(result, "content", result))[:1000],
            "ts": _now_iso(),
        },
    )
    return result


def _extract_token_usage(messages: list[Any]) -> dict[str, int] | None:
    """Pull `usage_metadata` off the last AIMessage in the response.

    Only emits when usage is actually populated (avoids spurious zero-counts
    during tests).
    """
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            meta = getattr(msg, "usage_metadata", None)
            if meta:
                return {
                    "prompt_tokens": meta.get("input_tokens", 0),
                    "completion_tokens": meta.get("output_tokens", 0),
                    "total_tokens": meta.get("total_tokens", 0),
                }
            break
    return None


@wrap_model_call
async def token_usage_emitter(request, handler):  # noqa: ANN001
    """Emit one `token_usage` SSE event per model turn when usage is reported."""
    response = await handler(request)
    messages = list(getattr(response, "result", []))
    usage = _extract_token_usage(messages)
    if usage is not None:
        case_id = _state_field(request.state, "case_id")
        agent_name = _state_field(request.state, "agent_name")
        await publish_agent_event(
            case_id,
            {
                "case_id": case_id,
                "agent": agent_name,
                "event": "token_usage",
                "usage": usage,
                "ts": _now_iso(),
            },
        )
    return response
