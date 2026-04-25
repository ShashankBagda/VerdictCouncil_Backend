"""Sprint 1 1.A1.2 — middleware unit tests.

Verifies the four LangChain middleware hooks emit the right SSE
events / cancel signals / audit rows. Tests stub the writers
(`publish_agent_event`, `append_audit_entry`, `check_cancel_flag`)
to keep the suite hermetic.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage

pytestmark = pytest.mark.asyncio


def _fake_state(case_id: str = "case-1", agent_name: str = "evidence-analysis") -> dict:
    """Mimic CaseAwareState for hooks that read case_id / agent_name from state."""
    return {"case_id": case_id, "agent_name": agent_name, "messages": []}


def _fake_tool_call_request(
    tool_name: str = "search_precedents",
    args: dict | None = None,
    state: dict | None = None,
) -> SimpleNamespace:
    """Stand-in for langgraph.prebuilt.tool_node.ToolCallRequest."""
    return SimpleNamespace(
        tool_call={
            "name": tool_name,
            "args": args or {"query": "fair use"},
            "id": "tc-1",
            "type": "tool_call",
        },
        tool=None,
        state=state or _fake_state(),
        runtime=None,
    )


# ---------------------------------------------------------------------------
# sse_bridge — sse_tool_emitter
# ---------------------------------------------------------------------------


async def test_sse_tool_emitter_publishes_call_and_result(monkeypatch):
    from src.pipeline.graph.middleware import sse_bridge

    captured: list[dict] = []

    async def _capture(case_id, event):
        captured.append({"case_id": case_id, **event})

    monkeypatch.setattr(sse_bridge, "publish_agent_event", _capture)

    request = _fake_tool_call_request()
    handler = AsyncMock(return_value=ToolMessage(content="hit", tool_call_id="tc-1"))

    result = await sse_bridge.sse_tool_emitter.awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert len(captured) == 2, f"expected tool_call + tool_result events, got {captured!r}"
    assert captured[0]["event"] == "tool_call"
    assert captured[0]["tool_name"] == "search_precedents"
    assert captured[0]["agent"] == "evidence-analysis"
    assert captured[1]["event"] == "tool_result"
    assert captured[1]["tool_name"] == "search_precedents"


# ---------------------------------------------------------------------------
# sse_bridge — token_usage_emitter
# ---------------------------------------------------------------------------


async def test_token_usage_emitter_publishes_usage_when_present(monkeypatch):
    from langchain.agents.middleware.types import ModelResponse

    from src.pipeline.graph.middleware import sse_bridge

    captured: list[dict] = []

    async def _capture(case_id, event):
        captured.append({"case_id": case_id, **event})

    monkeypatch.setattr(sse_bridge, "publish_agent_event", _capture)

    msg = AIMessage(
        content="done",
        usage_metadata={"input_tokens": 42, "output_tokens": 7, "total_tokens": 49},
    )
    request = SimpleNamespace(state=_fake_state())
    handler = AsyncMock(return_value=ModelResponse(result=[msg]))

    result = await sse_bridge.token_usage_emitter.awrap_model_call(request, handler)

    assert isinstance(result, ModelResponse)
    assert len(captured) == 1
    assert captured[0]["event"] == "token_usage"
    assert captured[0]["agent"] == "evidence-analysis"
    assert captured[0]["usage"] == {
        "prompt_tokens": 42,
        "completion_tokens": 7,
        "total_tokens": 49,
    }


async def test_token_usage_emitter_silent_when_metadata_absent(monkeypatch):
    from langchain.agents.middleware.types import ModelResponse

    from src.pipeline.graph.middleware import sse_bridge

    captured: list[dict] = []

    async def _capture(case_id, event):
        captured.append(event)

    monkeypatch.setattr(sse_bridge, "publish_agent_event", _capture)

    msg = AIMessage(content="done")  # no usage_metadata
    request = SimpleNamespace(state=_fake_state())
    handler = AsyncMock(return_value=ModelResponse(result=[msg]))

    await sse_bridge.token_usage_emitter.awrap_model_call(request, handler)

    assert captured == [], "no event should fire when usage_metadata is missing"


# ---------------------------------------------------------------------------
# cancellation — cancel_check (Sprint 4 4.A3.9 — saver-halt path)
# ---------------------------------------------------------------------------


async def test_cancel_check_passes_through_when_halt_unset():
    """No halt slot → middleware lets the agent loop continue."""
    from src.pipeline.graph.middleware import cancellation

    state = {"case_id": "case-1", "halt": None, "messages": []}
    runtime = SimpleNamespace()

    result = await cancellation.cancel_check.abefore_model(state, runtime)

    assert result is None, "no Command should be returned when halt is unset"


async def test_cancel_check_jumps_to_end_when_halt_set():
    """Halt slot populated → middleware short-circuits to end."""
    from langgraph.types import Command

    from src.pipeline.graph.middleware import cancellation

    state = {
        "case_id": "case-1",
        "halt": {"reason": "cancelled", "by": "judge-1"},
        "messages": [],
    }
    runtime = SimpleNamespace()

    result = await cancellation.cancel_check.abefore_model(state, runtime)

    assert isinstance(result, Command), f"expected Command, got {type(result).__name__}"
    assert result.goto == "end"


async def test_cancel_check_ignores_redis_after_cutover(monkeypatch):
    """Saver-halt is the only signal — middleware no longer reads Redis.

    Locks 4.A3.9 acceptance criterion ("Redis cancel-flag code path
    retired or neutralized"): even if a stale Redis flag is set, the
    middleware must not consult it. Reading from Redis in the agent
    loop is what we are removing.
    """
    from src.pipeline.graph.middleware import cancellation

    # If the middleware module still imports check_cancel_flag we'd be
    # back to the legacy path — fail loudly instead of silently passing.
    assert not hasattr(cancellation, "check_cancel_flag"), (
        "cancel_check must no longer depend on Redis check_cancel_flag; "
        "halt comes from saver state under 4.A3.9"
    )

    state = {"case_id": "case-1", "halt": None, "messages": []}
    runtime = SimpleNamespace()

    result = await cancellation.cancel_check.abefore_model(state, runtime)
    assert result is None


# ---------------------------------------------------------------------------
# audit — audit_tool_call
# ---------------------------------------------------------------------------


async def test_audit_tool_call_records_tool_invocation(monkeypatch):
    from src.pipeline.graph.middleware import audit as audit_mw

    captured: list[dict] = []

    async def _capture(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(audit_mw, "append_audit_entry", _capture)

    request = _fake_tool_call_request()
    handler = AsyncMock(return_value=ToolMessage(content="hit", tool_call_id="tc-1"))

    result = await audit_mw.audit_tool_call.awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert len(captured) == 1
    assert captured[0]["case_id"] == "case-1"
    assert captured[0]["agent_name"] == "evidence-analysis"
    assert captured[0]["action"] == "tool_call"
    assert captured[0]["input_payload"]["tool_name"] == "search_precedents"
    assert captured[0]["input_payload"]["args"] == {"query": "fair use"}
    # The result payload is best-effort serialization of the ToolMessage.
    assert "tool_result" in captured[0]["output_payload"]
