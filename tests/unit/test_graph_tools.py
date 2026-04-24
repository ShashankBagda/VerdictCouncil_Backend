"""Unit tests for src.pipeline.graph.tools — make_tools factory and side-channel."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("langchain_core", reason="langchain-core not installed")
from src.pipeline.graph.prompts import AGENT_TOOLS
from src.pipeline.graph.tools import PrecedentMetaSideChannel, make_tools
from src.shared.case_state import CaseState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(vector_store_id: str | None = "vs-test-123") -> dict:
    return {
        "case": CaseState(domain_vector_store_id=vector_store_id),
        "run_id": "run-1",
        "extra_instructions": {},
        "retry_counts": {},
        "halt": None,
        "mlflow_run_ids": {},
        "is_resume": False,
        "start_agent": None,
    }


# ---------------------------------------------------------------------------
# Tool subset per agent
# ---------------------------------------------------------------------------


class TestToolSubset:
    def test_legal_knowledge_gets_both_search_tools(self):
        state = _make_state()
        tools, _ = make_tools(state, "legal-knowledge")
        names = {t.name for t in tools}
        assert "search_precedents" in names
        assert "search_domain_guidance" in names

    def test_complexity_routing_gets_no_tools(self):
        state = _make_state()
        tools, _ = make_tools(state, "complexity-routing")
        assert tools == []

    def test_case_processing_gets_parse_document(self):
        state = _make_state()
        tools, _ = make_tools(state, "case-processing")
        names = {t.name for t in tools}
        assert names == {"parse_document"}

    def test_evidence_analysis_tools(self):
        state = _make_state()
        tools, _ = make_tools(state, "evidence-analysis")
        names = {t.name for t in tools}
        assert names == set(AGENT_TOOLS["evidence-analysis"])

    def test_fact_reconstruction_tools(self):
        state = _make_state()
        tools, _ = make_tools(state, "fact-reconstruction")
        names = {t.name for t in tools}
        assert names == set(AGENT_TOOLS["fact-reconstruction"])

    def test_witness_analysis_tools(self):
        state = _make_state()
        tools, _ = make_tools(state, "witness-analysis")
        names = {t.name for t in tools}
        assert names == set(AGENT_TOOLS["witness-analysis"])

    def test_argument_construction_tools(self):
        state = _make_state()
        tools, _ = make_tools(state, "argument-construction")
        names = {t.name for t in tools}
        assert names == set(AGENT_TOOLS["argument-construction"])

    def test_hearing_analysis_has_no_tools(self):
        state = _make_state()
        tools, _ = make_tools(state, "hearing-analysis")
        assert tools == []

    def test_hearing_governance_has_no_tools(self):
        state = _make_state()
        tools, _ = make_tools(state, "hearing-governance")
        assert tools == []

    def test_all_agents_have_correct_tool_count(self):
        state = _make_state()
        for agent_name, expected_tools in AGENT_TOOLS.items():
            tools, _ = make_tools(state, agent_name)
            assert len(tools) == len(expected_tools), (
                f"{agent_name}: expected {len(expected_tools)} tools, got {len(tools)}"
            )


# ---------------------------------------------------------------------------
# PrecedentMetaSideChannel
# ---------------------------------------------------------------------------


class TestPrecedentMetaSideChannel:
    def test_initially_none(self):
        ch = PrecedentMetaSideChannel()
        assert ch.metadata is None

    def test_first_record_sets_meta(self):
        ch = PrecedentMetaSideChannel()
        ch.record({"source_failed": False, "pair_status": "ok"})
        assert ch.metadata is not None
        assert ch.metadata["pair_status"] == "ok"

    def test_subsequent_non_failing_does_not_override(self):
        ch = PrecedentMetaSideChannel()
        ch.record({"source_failed": False, "pair_status": "ok"})
        ch.record({"source_failed": False, "pair_status": "different"})
        # Worst-of: first call wins unless source_failed
        assert ch.metadata["pair_status"] == "ok"

    def test_source_failed_escalates(self):
        ch = PrecedentMetaSideChannel()
        ch.record({"source_failed": False, "pair_status": "ok"})
        ch.record({"source_failed": True, "pair_status": "degraded"})
        assert ch.metadata["source_failed"] is True
        assert ch.metadata["pair_status"] == "degraded"

    def test_multiple_failures_accumulate(self):
        ch = PrecedentMetaSideChannel()
        ch.record({"source_failed": False, "pair_status": "ok"})
        ch.record({"source_failed": True, "pair_status": "degraded"})
        ch.record({"source_failed": False, "pair_status": "recovered"})
        # Once failed, stays failed
        assert ch.metadata["source_failed"] is True


# ---------------------------------------------------------------------------
# search_precedents — vector_store_id injection + metadata side-channel
# ---------------------------------------------------------------------------


class TestSearchPrecedentsTool:
    @pytest.mark.asyncio
    async def test_vector_store_id_injected(self):
        state = _make_state(vector_store_id="vs-999")
        tools, side_ch = make_tools(state, "legal-knowledge")
        sp_tool = next(t for t in tools if t.name == "search_precedents")

        mock_meta = MagicMock()
        mock_meta.source_failed = False
        mock_meta.pair_status = "ok"

        mock_result = MagicMock()
        mock_result.precedents = [{"case": "Test v Test"}]
        mock_result.metadata = {"source_failed": False, "pair_status": "ok"}

        with patch(
            "src.tools.search_precedents.search_precedents_with_meta",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_fn:
            result = await sp_tool.ainvoke({"query": "speeding fine", "domain": "traffic"})

        mock_fn.assert_called_once_with(
            query="speeding fine",
            domain="traffic",
            max_results=5,
            vector_store_id="vs-999",
        )
        assert result == [{"case": "Test v Test"}]
        assert side_ch.metadata == {"source_failed": False, "pair_status": "ok"}

    @pytest.mark.asyncio
    async def test_none_vector_store_id_passed_through(self):
        state = _make_state(vector_store_id=None)
        tools, _ = make_tools(state, "legal-knowledge")
        sp_tool = next(t for t in tools if t.name == "search_precedents")

        mock_result = MagicMock()
        mock_result.precedents = []
        mock_result.metadata = {"source_failed": True, "pair_status": "no_store"}

        with patch(
            "src.tools.search_precedents.search_precedents_with_meta",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_fn:
            await sp_tool.ainvoke({"query": "test", "domain": "small_claims"})

        mock_fn.assert_called_once_with(
            query="test",
            domain="small_claims",
            max_results=5,
            vector_store_id=None,
        )


# ---------------------------------------------------------------------------
# search_domain_guidance — vector_store_id injection + missing-store error
# ---------------------------------------------------------------------------


class TestSearchDomainGuidanceTool:
    @pytest.mark.asyncio
    async def test_vector_store_id_injected(self):
        state = _make_state(vector_store_id="vs-abc")
        tools, _ = make_tools(state, "legal-knowledge")
        sdg_tool = next(t for t in tools if t.name == "search_domain_guidance")

        with patch(
            "src.tools.search_domain_guidance.search_domain_guidance",
            new_callable=AsyncMock,
            return_value=[{"citation": "Act s.1"}],
        ) as mock_fn:
            result = await sdg_tool.ainvoke({"query": "speed limits"})

        mock_fn.assert_called_once_with(
            query="speed limits",
            vector_store_id="vs-abc",
            max_results=5,
        )
        assert result == [{"citation": "Act s.1"}]

    @pytest.mark.asyncio
    async def test_raises_when_no_vector_store(self):
        from src.tools.exceptions import DomainGuidanceUnavailable

        state = _make_state(vector_store_id=None)
        tools, _ = make_tools(state, "legal-knowledge")
        sdg_tool = next(t for t in tools if t.name == "search_domain_guidance")

        with pytest.raises(DomainGuidanceUnavailable):
            await sdg_tool.ainvoke({"query": "test"})
