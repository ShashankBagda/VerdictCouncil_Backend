"""SSE smoke tests for the LangGraph pipeline.

Covers:
  - _is_terminal_event: stream-close detection logic
  - subscribe(): terminates when a terminal payload is published
  - astream_graph_events(): thin wrapper over compiled.astream_events
  - publish_progress / publish_agent_event: event shapes emitted by
    _run_agent_node (started / agent_completed / completed lifecycle)
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.pipeline_events import _is_terminal_event

# ---------------------------------------------------------------------------
# _is_terminal_event
# ---------------------------------------------------------------------------


class TestIsTerminalEvent:
    def test_hearing_governance_completed_is_terminal(self):
        assert _is_terminal_event({"agent": "hearing-governance", "phase": "completed"})

    def test_hearing_governance_failed_is_terminal(self):
        assert _is_terminal_event({"agent": "hearing-governance", "phase": "failed"})

    def test_pipeline_terminal_is_terminal(self):
        assert _is_terminal_event({"agent": "pipeline", "phase": "terminal"})

    def test_pipeline_awaiting_review_is_terminal(self):
        assert _is_terminal_event({"agent": "pipeline", "phase": "awaiting_review"})

    def test_agent_started_is_not_terminal(self):
        assert not _is_terminal_event({"agent": "case-processing", "phase": "started"})

    def test_agent_completed_is_not_terminal(self):
        assert not _is_terminal_event({"agent": "case-processing", "phase": "completed"})

    def test_evidence_analysis_completed_is_not_terminal(self):
        assert not _is_terminal_event({"agent": "evidence-analysis", "phase": "completed"})

    def test_missing_agent_key_is_not_terminal(self):
        assert not _is_terminal_event({"phase": "completed"})

    def test_empty_dict_is_not_terminal(self):
        assert not _is_terminal_event({})


# ---------------------------------------------------------------------------
# subscribe(): generator closes on terminal events
# ---------------------------------------------------------------------------


class TestSubscribeTermination:
    @pytest.mark.asyncio
    async def test_subscribe_stops_on_pipeline_awaiting_review(self):
        """subscribe() must stop yielding after pipeline/awaiting_review."""
        events = [
            json.dumps({"agent": "case-processing", "phase": "started"}),
            json.dumps({"agent": "case-processing", "phase": "completed"}),
            json.dumps({"agent": "pipeline", "phase": "awaiting_review"}),
            json.dumps({"agent": "would-not-be-seen", "phase": "started"}),
        ]
        collected: list[str] = []

        async def _fake_subscribe(case_id: str) -> AsyncGenerator[str, None]:
            for raw in events:
                yield raw
                parsed = json.loads(raw)
                if _is_terminal_event(parsed):
                    return

        async for payload in _fake_subscribe("case-abc"):
            collected.append(payload)

        assert len(collected) == 3
        assert json.loads(collected[-1])["phase"] == "awaiting_review"

    @pytest.mark.asyncio
    async def test_subscribe_stops_on_pipeline_terminal(self):
        """subscribe() closes on pipeline/terminal (halt path)."""
        events = [
            json.dumps({"agent": "case-processing", "phase": "started"}),
            json.dumps({"agent": "pipeline", "phase": "terminal"}),
            json.dumps({"agent": "should-not-appear", "phase": "started"}),
        ]
        collected: list[str] = []

        async def _fake_subscribe(case_id: str) -> AsyncGenerator[str, None]:
            for raw in events:
                yield raw
                parsed = json.loads(raw)
                if _is_terminal_event(parsed):
                    return

        async for payload in _fake_subscribe("case-abc"):
            collected.append(payload)

        assert len(collected) == 2

    @pytest.mark.asyncio
    async def test_subscribe_stops_on_hearing_governance_completed(self):
        """Happy-path close: hearing-governance/completed ends the stream."""
        events = [
            json.dumps({"agent": "hearing-governance", "phase": "started"}),
            json.dumps({"agent": "hearing-governance", "phase": "completed"}),
        ]
        collected: list[str] = []

        async def _fake_subscribe(case_id: str) -> AsyncGenerator[str, None]:
            for raw in events:
                yield raw
                parsed = json.loads(raw)
                if _is_terminal_event(parsed):
                    return

        async for payload in _fake_subscribe("case-abc"):
            collected.append(payload)

        assert len(collected) == 2


# ---------------------------------------------------------------------------
# astream_graph_events wrapper
# ---------------------------------------------------------------------------


class TestAstreamGraphEvents:
    @pytest.mark.asyncio
    async def test_yields_events_from_compiled_graph(self):
        """astream_graph_events yields every event the graph produces."""
        from src.pipeline.graph.sse import astream_graph_events

        fake_events = [
            {"event": "on_chain_start", "name": "case_processing"},
            {"event": "on_chain_end", "name": "case_processing"},
        ]

        async def _fake_astream_events(input_state, config, version):
            for ev in fake_events:
                yield ev

        mock_graph = MagicMock()
        mock_graph.astream_events = _fake_astream_events

        collected: list[dict] = []
        async for ev in astream_graph_events(mock_graph, {}, {}):
            collected.append(ev)

        assert collected == fake_events

    @pytest.mark.asyncio
    async def test_empty_graph_yields_nothing(self):
        from src.pipeline.graph.sse import astream_graph_events

        async def _empty(input_state, config, version):
            return
            yield  # make it an async generator

        mock_graph = MagicMock()
        mock_graph.astream_events = _empty

        collected: list[dict] = []
        async for ev in astream_graph_events(mock_graph, {}, {}):
            collected.append(ev)

        assert collected == []


# ---------------------------------------------------------------------------
# SSE event lifecycle from _run_agent_node
# ---------------------------------------------------------------------------


class TestNodeCoreSSEEvents:
    """Verify that _run_agent_node emits the expected SSE event sequence."""

    @staticmethod
    def _make_state(agent_name: str = "evidence-analysis") -> dict[str, Any]:
        from src.shared.case_state import CaseState

        return {
            "case": CaseState(domain="traffic_violation"),  # type: ignore[arg-type]
            "run_id": "run-sse-test",
            "extra_instructions": {},
            "retry_counts": {},
            "halt": None,
            "mlflow_run_ids": {},
            "is_resume": False,
            "start_agent": None,
        }

    @pytest.mark.asyncio
    async def test_started_then_completed_progress_events(self):
        """publish_progress must be called: started → completed, in order."""
        import json
        from unittest.mock import MagicMock

        from langchain_core.messages import AIMessage

        from src.pipeline.graph.nodes.common import _run_agent_node

        llm_response = AIMessage(content=json.dumps({"evidence_analysis": {"evidence_items": []}}))
        llm_mock = MagicMock()
        llm_mock.bind_tools.return_value = llm_mock
        llm_mock.ainvoke = AsyncMock(return_value=llm_response)

        with (
            patch("src.pipeline.graph.nodes.common.ChatOpenAI", return_value=llm_mock),
            patch(
                "src.pipeline.graph.nodes.common.publish_progress",
                new_callable=AsyncMock,
            ) as mock_progress,
            patch("src.pipeline.graph.nodes.common.publish_agent_event", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.agent_run") as mock_ar,
            patch("src.pipeline.graph.nodes.common.persist_case_state", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.async_session") as mock_session,
        ):
            mock_ar.return_value.__enter__ = MagicMock(return_value=None)
            mock_ar.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            await _run_agent_node("evidence-analysis", self._make_state())

        phases = [c.args[0].phase for c in mock_progress.call_args_list]
        assert phases == ["started", "completed"]

    @pytest.mark.asyncio
    async def test_agent_completed_event_emitted(self):
        """publish_agent_event must include an agent_completed entry."""
        import json
        from unittest.mock import MagicMock

        from langchain_core.messages import AIMessage

        from src.pipeline.graph.nodes.common import _run_agent_node

        llm_response = AIMessage(content=json.dumps({}))
        llm_mock = MagicMock()
        llm_mock.bind_tools.return_value = llm_mock
        llm_mock.ainvoke = AsyncMock(return_value=llm_response)

        with (
            patch("src.pipeline.graph.nodes.common.ChatOpenAI", return_value=llm_mock),
            patch("src.pipeline.graph.nodes.common.publish_progress", new_callable=AsyncMock),
            patch(
                "src.pipeline.graph.nodes.common.publish_agent_event",
                new_callable=AsyncMock,
            ) as mock_agent_ev,
            patch("src.pipeline.graph.nodes.common.agent_run") as mock_ar,
            patch("src.pipeline.graph.nodes.common.persist_case_state", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.async_session") as mock_session,
        ):
            mock_ar.return_value.__enter__ = MagicMock(return_value=None)
            mock_ar.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            await _run_agent_node("evidence-analysis", self._make_state())

        events_emitted = [c.args[1].get("event") for c in mock_agent_ev.call_args_list]
        assert "agent_completed" in events_emitted

    @pytest.mark.asyncio
    async def test_started_event_carries_correct_agent_name(self):
        """The started PipelineProgressEvent must name the correct agent."""
        import json
        from unittest.mock import MagicMock

        from langchain_core.messages import AIMessage

        from src.pipeline.graph.nodes.common import _run_agent_node

        llm_response = AIMessage(content=json.dumps({}))
        llm_mock = MagicMock()
        llm_mock.bind_tools.return_value = llm_mock
        llm_mock.ainvoke = AsyncMock(return_value=llm_response)

        with (
            patch("src.pipeline.graph.nodes.common.ChatOpenAI", return_value=llm_mock),
            patch(
                "src.pipeline.graph.nodes.common.publish_progress",
                new_callable=AsyncMock,
            ) as mock_progress,
            patch("src.pipeline.graph.nodes.common.publish_agent_event", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.agent_run") as mock_ar,
            patch("src.pipeline.graph.nodes.common.persist_case_state", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.async_session") as mock_session,
        ):
            mock_ar.return_value.__enter__ = MagicMock(return_value=None)
            mock_ar.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            await _run_agent_node("witness-analysis", self._make_state())

        started_event = mock_progress.call_args_list[0].args[0]
        assert started_event.agent == "witness-analysis"
        assert started_event.phase == "started"
