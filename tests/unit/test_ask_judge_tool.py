"""Unit tests for the `ask_judge` tool (Q1.11 chat-steering).

The tool is the v1 surface for agent-initiated questions to the judge.
Contract:
  - Calling the tool fires `langgraph.types.interrupt` with a payload
    carrying `kind="ask_judge"`, the verbatim question, and a fresh
    UUID `interrupt_id`.
  - The tool returns whatever text the resume payload carries, so the
    LLM can incorporate the judge's answer on its next step.

These tests stub out `interrupt()` because the real symbol expects a
LangGraph runtime context (a Pregel loop). The shape and ID generation
are what matter for the wire contract.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.pipeline.graph.state import GraphState
from src.pipeline.graph.tools import make_tools
from src.shared.case_state import CaseState


def _patch_publish() -> Any:
    """Stub publish_agent_event — the tool emits an SSE frame before
    interrupt(), and Redis isn't available in unit tests. The tool
    imports it inside its body so we patch the source module directly."""
    return patch(
        "src.services.pipeline_events.publish_agent_event",
        new=AsyncMock(return_value=None),
    )


def _state(domain_vector_store_id: str | None = None) -> GraphState:
    case = CaseState(domain_vector_store_id=domain_vector_store_id)
    return GraphState(  # type: ignore[typeddict-item]
        case=case,
        run_id="r1",
        extra_instructions={},
        retry_counts={},
        halt=None,
        research_parts={},
        retrieved_source_ids={},
        research_output=None,
        intake_output=None,
        synthesis_output=None,
        audit_output=None,
        pending_action=None,
        is_resume=False,
        start_agent=None,
        judge_messages=[],
    )


def _get_ask_judge_tool() -> Any:
    """Resolve the `ask_judge` tool from the make_tools aggregate.

    `argument-construction` is the legacy AGENT_TOOLS entry that lists
    `ask_judge` (the new-topology PHASE_TOOL_NAMES["synthesis"] does
    too, but make_tools filters by the legacy map). One of these entries
    is enough for the aggregator to surface the tool.
    """
    tools, _ = make_tools(_state(), agent_name="argument-construction")
    by_name = {t.name: t for t in tools}
    assert "ask_judge" in by_name, "ask_judge must be registered under argument-construction"
    return by_name["ask_judge"]


class TestAskJudgeTool:
    @pytest.mark.asyncio
    async def test_invocation_fires_interrupt_with_correct_shape(self):
        """The tool body must call `interrupt({...})` with kind=ask_judge,
        the question verbatim, and a UUID interrupt_id."""
        tool = _get_ask_judge_tool()
        captured: dict[str, Any] = {}

        def fake_interrupt(payload: dict[str, Any]) -> str:
            captured.update(payload)
            return "judge said B"

        with _patch_publish(), patch("langgraph.types.interrupt", side_effect=fake_interrupt):
            result = await tool.ainvoke({"question": "Reading A or B?"})

        assert captured["kind"] == "ask_judge"
        assert captured["question"] == "Reading A or B?"
        assert isinstance(captured["interrupt_id"], str)
        assert len(captured["interrupt_id"]) == 32  # uuid4().hex
        assert result == "judge said B"

    @pytest.mark.asyncio
    async def test_dict_resume_payload_returns_text_field(self):
        """When the /respond endpoint resumes with a dict like
        `{"text": "..."}`, the tool returns the `text` value as a plain
        string so the LLM sees a clean ToolMessage, not a serialised dict."""
        tool = _get_ask_judge_tool()

        def fake_interrupt(payload: dict[str, Any]) -> dict[str, Any]:
            return {"text": "go with B", "extra": "ignored"}

        with _patch_publish(), patch("langgraph.types.interrupt", side_effect=fake_interrupt):
            result = await tool.ainvoke({"question": "?"})

        assert result == "go with B"

    @pytest.mark.asyncio
    async def test_string_resume_payload_passes_through(self):
        """Defensive: if a future resume path sends a bare string, the
        tool must still return a string and not blow up on .get(...)."""
        tool = _get_ask_judge_tool()

        with (
            _patch_publish(),
            patch("langgraph.types.interrupt", side_effect=lambda _: "bare string reply"),
        ):
            result = await tool.ainvoke({"question": "?"})

        assert result == "bare string reply"

    @pytest.mark.asyncio
    async def test_each_invocation_mints_unique_interrupt_id(self):
        """Multi-turn within a phase (Q4 default = allowed) requires
        each interrupt to carry its own id so /respond can match the
        right pending interrupt and 409 on stale double-sends."""
        tool = _get_ask_judge_tool()
        ids: list[str] = []

        def fake_interrupt(payload: dict[str, Any]) -> str:
            ids.append(payload["interrupt_id"])
            return ""

        with _patch_publish(), patch("langgraph.types.interrupt", side_effect=fake_interrupt):
            await tool.ainvoke({"question": "Q1"})
            await tool.ainvoke({"question": "Q2"})
            await tool.ainvoke({"question": "Q3"})

        assert len(set(ids)) == 3, "interrupt_id collisions would break resume routing"

    @pytest.mark.asyncio
    async def test_emits_awaiting_input_sse_before_interrupt(self):
        """The frontend mounts the chat input on receipt of the
        AgentAwaitingInputEvent SSE frame — the emission must happen
        BEFORE interrupt() pauses the graph, otherwise the UI would not
        know to display the question until the next polling tick."""
        tool = _get_ask_judge_tool()
        emit_calls: list[dict[str, Any]] = []
        order: list[str] = []

        async def fake_publish(_case_id: str, payload: dict[str, Any]) -> None:
            emit_calls.append(payload)
            order.append("publish")

        def fake_interrupt(payload: dict[str, Any]) -> str:
            order.append("interrupt")
            return "ack"

        with (
            patch("src.services.pipeline_events.publish_agent_event", new=fake_publish),
            patch("langgraph.types.interrupt", side_effect=fake_interrupt),
        ):
            await tool.ainvoke({"question": "Q?"})

        assert order == ["publish", "interrupt"], "SSE emit must precede interrupt"
        assert len(emit_calls) == 1
        emitted = emit_calls[0]
        assert emitted["kind"] == "interrupt"
        assert emitted["agent"] == "synthesis"
        assert emitted["question"] == "Q?"
        assert isinstance(emitted["interrupt_id"], str)
        assert len(emitted["interrupt_id"]) == 32

    def test_synthesis_phase_includes_ask_judge(self):
        """PHASE_TOOL_NAMES wiring sanity — synthesis must see the tool."""
        from src.pipeline.graph.agents.factory import PHASE_TOOL_NAMES

        assert "ask_judge" in PHASE_TOOL_NAMES["synthesis"]
        assert "ask_judge" not in PHASE_TOOL_NAMES["intake"]
        assert "ask_judge" not in PHASE_TOOL_NAMES["audit"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
