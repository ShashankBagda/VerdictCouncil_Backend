"""P1.7 acceptance — tool loop terminates at MAX_TOOL_ITERATIONS.

Verifies that _run_agent_node exits the LLM+tool loop after 10 iterations
even when the model keeps emitting tool calls on every turn.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.pipeline.graph.nodes.common import _run_agent_node
from src.shared.case_state import CaseState


def _make_tool_calling_ai_msg() -> AIMessage:
    """AIMessage that always has a tool call for 'fake_tool'."""
    msg = MagicMock(spec=AIMessage)
    msg.content = ""
    msg.tool_calls = [{"name": "fake_tool", "args": {}, "id": "tc-1"}]
    msg.usage_metadata = None
    return msg


def _make_terminal_ai_msg() -> AIMessage:
    """AIMessage with no tool calls and valid JSON content."""
    msg = MagicMock(spec=AIMessage)
    msg.content = "{}"
    msg.tool_calls = []
    msg.usage_metadata = None
    return msg


def _make_state(case_id: str | None = None) -> dict:
    case = CaseState(case_id=case_id or str(uuid.uuid4()))
    return {
        "case": case,
        "run_id": "test-run-id",
        "extra_instructions": {},
        "retry_counts": {},
        "halt": None,
        "mlflow_run_ids": {},
        "is_resume": False,
        "start_agent": None,
    }


@pytest.mark.asyncio
class TestToolLoopCap:
    async def test_loop_exits_after_max_iterations(self):
        """When the model keeps returning tool calls, the loop must exit after
        MAX_TOOL_ITERATIONS (10) even though the model never goes tool-free."""
        state = _make_state()

        # LLM always returns a tool-calling message (infinite loop without cap)
        infinite_ai_msg = _make_tool_calling_ai_msg()
        llm_mock = AsyncMock(return_value=infinite_ai_msg)

        fake_tool = MagicMock()
        fake_tool.name = "fake_tool"
        fake_tool.ainvoke = AsyncMock(return_value={"ok": True})

        llm_bound = MagicMock()
        llm_bound.ainvoke = llm_mock
        llm_bound.bind_tools = MagicMock(return_value=llm_bound)

        with (
            patch(
                "src.pipeline.graph.nodes.common.ChatOpenAI",
                return_value=llm_bound,
            ),
            patch(
                "src.pipeline.graph.nodes.common.make_tools",
                return_value=([fake_tool], MagicMock(metadata=None)),
            ),
            patch("src.pipeline.graph.nodes.common.publish_progress", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.publish_agent_event", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.check_cancel_flag", return_value=False),
            patch(
                "src.pipeline.graph.nodes.common.agent_run",
                return_value=MagicMock(
                    __enter__=MagicMock(return_value=None),
                    __exit__=MagicMock(return_value=False),
                ),
            ),
            patch("src.pipeline.graph.nodes.common.persist_case_state", new_callable=AsyncMock),
            patch(
                "src.pipeline.graph.nodes.common.async_session",
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=AsyncMock()),
                    __aexit__=AsyncMock(return_value=False),
                ),
            ),
            patch("src.pipeline.graph.nodes.common._resolve_model", return_value="gpt-4o"),
            patch(
                "src.pipeline.graph.nodes.common.normalize_agent_output",
                side_effect=lambda agent, output: output,
            ),
            patch(
                "src.pipeline.graph.nodes.common.validate_field_ownership",
                return_value=None,
            ),
            patch(
                "src.pipeline.graph.nodes.common.append_audit_entry",
                side_effect=lambda case, **kw: case,
            ),
        ):
            result = await _run_agent_node("case-processing", state)

        # Tool was invoked at most 10 times (one per iteration)
        assert fake_tool.ainvoke.call_count <= 10, (
            f"Tool called {fake_tool.ainvoke.call_count} times; cap should be 10"
        )
        # Node must return a valid state delta (no exception)
        assert "case" in result

    async def test_loop_completes_normally_within_cap(self):
        """When the model naturally stops calling tools before the cap, the loop
        exits normally without triggering the iteration limit."""
        state = _make_state()

        call_count = 0

        async def _side_effect(messages, **kw):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return _make_tool_calling_ai_msg()
            return _make_terminal_ai_msg()

        fake_tool = MagicMock()
        fake_tool.name = "fake_tool"
        fake_tool.ainvoke = AsyncMock(return_value={"ok": True})

        llm_mock = AsyncMock(side_effect=_side_effect)
        llm_bound = MagicMock()
        llm_bound.ainvoke = llm_mock
        llm_bound.bind_tools = MagicMock(return_value=llm_bound)

        with (
            patch("src.pipeline.graph.nodes.common.ChatOpenAI", return_value=llm_bound),
            patch(
                "src.pipeline.graph.nodes.common.make_tools",
                return_value=([fake_tool], MagicMock(metadata=None)),
            ),
            patch("src.pipeline.graph.nodes.common.publish_progress", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.publish_agent_event", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.check_cancel_flag", return_value=False),
            patch(
                "src.pipeline.graph.nodes.common.agent_run",
                return_value=MagicMock(
                    __enter__=MagicMock(return_value=None),
                    __exit__=MagicMock(return_value=False),
                ),
            ),
            patch("src.pipeline.graph.nodes.common.persist_case_state", new_callable=AsyncMock),
            patch(
                "src.pipeline.graph.nodes.common.async_session",
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=AsyncMock()),
                    __aexit__=AsyncMock(return_value=False),
                ),
            ),
            patch("src.pipeline.graph.nodes.common._resolve_model", return_value="gpt-4o"),
            patch(
                "src.pipeline.graph.nodes.common.normalize_agent_output",
                side_effect=lambda agent, output: output,
            ),
            patch(
                "src.pipeline.graph.nodes.common.validate_field_ownership",
                return_value=None,
            ),
            patch(
                "src.pipeline.graph.nodes.common.append_audit_entry",
                side_effect=lambda case, **kw: case,
            ),
        ):
            result = await _run_agent_node("case-processing", state)

        # Two tool calls then stop — cap never triggered
        assert fake_tool.ainvoke.call_count == 2
        assert "case" in result
