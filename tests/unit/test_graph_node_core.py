"""Unit tests for src.pipeline.graph.nodes.common._run_agent_node."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("langchain_core", reason="langchain-core not installed")
from langchain_core.messages import AIMessage

from src.pipeline.graph.nodes.common import _run_agent_node
from src.shared.case_state import CaseState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EVIDENCE_AGENT = "evidence-analysis"


def _make_state(extra: dict | None = None) -> dict:
    base: dict[str, Any] = {
        "case": CaseState(domain="traffic_violation"),  # type: ignore[arg-type]
        "run_id": "run-test-1",
        "extra_instructions": {},
        "retry_counts": {},
        "halt": None,
        "mlflow_run_ids": {},
        "is_resume": False,
        "start_agent": None,
    }
    if extra:
        base.update(extra)
    return base


def _ai_msg(content: str, tool_calls: list | None = None) -> AIMessage:
    msg = AIMessage(content=content)
    if tool_calls:
        msg.tool_calls = tool_calls  # type: ignore[assignment]
    return msg


def _mock_llm(responses: list[AIMessage]):
    """Return a mock ChatOpenAI that yields responses in order."""
    llm_mock = MagicMock()
    llm_mock.bind_tools.return_value = llm_mock
    llm_mock.ainvoke = AsyncMock(side_effect=responses)
    return llm_mock


# ---------------------------------------------------------------------------
# Happy path — no tool calls
# ---------------------------------------------------------------------------


class TestRunAgentNodeNoTools:
    @pytest.mark.asyncio
    async def test_returns_updated_case(self):
        ev_output = {
            "evidence_analysis": {
                "evidence_items": [{"id": "e1", "type": "photo"}],
                "exhibits": [],
                "credibility_scores": {},
            }
        }
        llm_response = _ai_msg(content=json.dumps(ev_output))

        with (
            patch(
                "src.pipeline.graph.nodes.common.ChatOpenAI",
                return_value=_mock_llm([llm_response]),
            ),
            patch("src.pipeline.graph.nodes.common.publish_progress", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.publish_agent_event", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.agent_run") as mock_ar,
            patch("src.pipeline.graph.nodes.common.persist_case_state", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.async_session") as mock_session,
        ):
            mock_ar.return_value.__enter__ = MagicMock(return_value=None)
            mock_ar.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            state = _make_state()
            result = await _run_agent_node(_EVIDENCE_AGENT, state)

        assert "case" in result
        updated: CaseState = result["case"]
        assert updated.evidence_analysis is not None
        assert len(updated.evidence_analysis.evidence_items) == 1

    @pytest.mark.asyncio
    async def test_audit_entry_appended(self):
        llm_response = _ai_msg(content=json.dumps({"evidence_analysis": {"evidence_items": []}}))

        with (
            patch(
                "src.pipeline.graph.nodes.common.ChatOpenAI", return_value=_mock_llm([llm_response])
            ),  # noqa: E501
            patch("src.pipeline.graph.nodes.common.publish_progress", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.publish_agent_event", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.agent_run") as mock_ar,
            patch("src.pipeline.graph.nodes.common.persist_case_state", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.async_session") as mock_session,
        ):
            mock_ar.return_value.__enter__ = MagicMock(return_value=None)
            mock_ar.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            state = _make_state()
            result = await _run_agent_node(_EVIDENCE_AGENT, state)

        updated: CaseState = result["case"]
        assert len(updated.audit_log) == 1
        assert updated.audit_log[0].agent == _EVIDENCE_AGENT

    @pytest.mark.asyncio
    async def test_publish_progress_called_twice(self):
        """publish_progress must be called with started then completed."""
        llm_response = _ai_msg(content=json.dumps({"evidence_analysis": {}}))

        with (
            patch(
                "src.pipeline.graph.nodes.common.ChatOpenAI", return_value=_mock_llm([llm_response])
            ),  # noqa: E501
            patch(
                "src.pipeline.graph.nodes.common.publish_progress",
                new_callable=AsyncMock,
            ) as mock_pub,
            patch("src.pipeline.graph.nodes.common.publish_agent_event", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.agent_run") as mock_ar,
            patch("src.pipeline.graph.nodes.common.persist_case_state", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.async_session") as mock_session,
        ):
            mock_ar.return_value.__enter__ = MagicMock(return_value=None)
            mock_ar.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            await _run_agent_node(_EVIDENCE_AGENT, _make_state())

        phases = [call.args[0].phase for call in mock_pub.call_args_list]
        assert phases == ["started", "completed"]

    @pytest.mark.asyncio
    async def test_extra_instructions_appended_to_prompt(self):
        """Extra instructions are appended to the system prompt before the LLM call."""
        llm_response = _ai_msg(content=json.dumps({}))
        llm_mock = _mock_llm([llm_response])

        with (
            patch("src.pipeline.graph.nodes.common.ChatOpenAI", return_value=llm_mock),
            patch("src.pipeline.graph.nodes.common.publish_progress", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.publish_agent_event", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.agent_run") as mock_ar,
            patch("src.pipeline.graph.nodes.common.persist_case_state", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.async_session") as mock_session,
        ):
            mock_ar.return_value.__enter__ = MagicMock(return_value=None)
            mock_ar.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            state = _make_state(
                {"extra_instructions": {_EVIDENCE_AGENT: "Focus on document contradictions."}}
            )
            await _run_agent_node(_EVIDENCE_AGENT, state)

        call_args = llm_mock.ainvoke.call_args_list[0][0][0]
        system_content = call_args[0].content
        assert "Focus on document contradictions." in system_content


# ---------------------------------------------------------------------------
# Field ownership enforcement
# ---------------------------------------------------------------------------


class TestFieldOwnership:
    @pytest.mark.asyncio
    async def test_unauthorized_field_stripped(self):
        """If agent writes a field it doesn't own, the field is stripped."""
        # evidence-analysis does NOT own hearing_analysis
        bad_output = {
            "evidence_analysis": {"evidence_items": []},
            "hearing_analysis": {"preliminary_conclusion": "guilty"},
        }
        llm_response = _ai_msg(content=json.dumps(bad_output))

        with (
            patch(
                "src.pipeline.graph.nodes.common.ChatOpenAI", return_value=_mock_llm([llm_response])
            ),  # noqa: E501
            patch("src.pipeline.graph.nodes.common.publish_progress", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.publish_agent_event", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.agent_run") as mock_ar,
            patch("src.pipeline.graph.nodes.common.persist_case_state", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.async_session") as mock_session,
        ):
            mock_ar.return_value.__enter__ = MagicMock(return_value=None)
            mock_ar.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _run_agent_node(_EVIDENCE_AGENT, _make_state())

        updated: CaseState = result["case"]
        assert updated.hearing_analysis is None


# ---------------------------------------------------------------------------
# Precedent metadata side-channel (legal-knowledge agent)
# ---------------------------------------------------------------------------


class TestPrecedentMetaFolding:
    @pytest.mark.asyncio
    async def test_precedent_meta_folded_into_case(self):
        """legal-knowledge node folds precedent metadata into case state."""
        from src.pipeline.graph.tools import PrecedentMetaSideChannel

        llm_response = _ai_msg(content=json.dumps({"legal_rules": [{"rule": "Speed limit"}]}))

        mock_meta = PrecedentMetaSideChannel()
        mock_meta.record({"source_failed": False, "pair_status": "ok"})

        with (
            patch(
                "src.pipeline.graph.nodes.common.ChatOpenAI", return_value=_mock_llm([llm_response])
            ),  # noqa: E501
            patch("src.pipeline.graph.nodes.common.publish_progress", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.publish_agent_event", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.agent_run") as mock_ar,
            patch("src.pipeline.graph.nodes.common.persist_case_state", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.async_session") as mock_session,
            patch(
                "src.pipeline.graph.nodes.common.make_tools",
                return_value=([], mock_meta),
            ),
        ):
            mock_ar.return_value.__enter__ = MagicMock(return_value=None)
            mock_ar.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _run_agent_node("legal-knowledge", _make_state())

        updated: CaseState = result["case"]
        assert updated.precedent_source_metadata == {"source_failed": False, "pair_status": "ok"}


# ---------------------------------------------------------------------------
# MLflow run IDs propagated
# ---------------------------------------------------------------------------


class TestMlflowIds:
    @pytest.mark.asyncio
    async def test_mlflow_ids_returned_when_available(self):
        llm_response = _ai_msg(content=json.dumps({}))

        with (
            patch(
                "src.pipeline.graph.nodes.common.ChatOpenAI", return_value=_mock_llm([llm_response])
            ),  # noqa: E501
            patch("src.pipeline.graph.nodes.common.publish_progress", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.publish_agent_event", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.agent_run") as mock_ar,
            patch("src.pipeline.graph.nodes.common.persist_case_state", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.async_session") as mock_session,
        ):
            # Simulate MLflow returning run IDs
            mock_ar.return_value.__enter__ = MagicMock(return_value=("mlrun-123", "exp-456"))
            mock_ar.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _run_agent_node(_EVIDENCE_AGENT, _make_state())

        assert "mlflow_run_ids" in result
        assert result["mlflow_run_ids"][_EVIDENCE_AGENT] == ("mlrun-123", "exp-456")

    @pytest.mark.asyncio
    async def test_mlflow_ids_absent_when_mlflow_disabled(self):
        llm_response = _ai_msg(content=json.dumps({}))

        with (
            patch(
                "src.pipeline.graph.nodes.common.ChatOpenAI", return_value=_mock_llm([llm_response])
            ),  # noqa: E501
            patch("src.pipeline.graph.nodes.common.publish_progress", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.publish_agent_event", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.agent_run") as mock_ar,
            patch("src.pipeline.graph.nodes.common.persist_case_state", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.async_session") as mock_session,
        ):
            mock_ar.return_value.__enter__ = MagicMock(return_value=None)  # None = MLflow off
            mock_ar.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _run_agent_node(_EVIDENCE_AGENT, _make_state())

        assert "mlflow_run_ids" not in result


# ---------------------------------------------------------------------------
# Persist failure is non-fatal
# ---------------------------------------------------------------------------


class TestPersistFault:
    @pytest.mark.asyncio
    async def test_persist_failure_does_not_abort_node(self):
        llm_response = _ai_msg(content=json.dumps({}))

        with (
            patch(
                "src.pipeline.graph.nodes.common.ChatOpenAI", return_value=_mock_llm([llm_response])
            ),  # noqa: E501
            patch("src.pipeline.graph.nodes.common.publish_progress", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.publish_agent_event", new_callable=AsyncMock),
            patch("src.pipeline.graph.nodes.common.agent_run") as mock_ar,
            patch(
                "src.pipeline.graph.nodes.common.persist_case_state",
                new_callable=AsyncMock,
                side_effect=Exception("DB down"),
            ),
            patch("src.pipeline.graph.nodes.common.async_session") as mock_session,
        ):
            mock_ar.return_value.__enter__ = MagicMock(return_value=None)
            mock_ar.return_value.__exit__ = MagicMock(return_value=False)
            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _run_agent_node(_EVIDENCE_AGENT, _make_state())

        assert "case" in result
