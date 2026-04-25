"""Tool artifact-format tests — Sprint 3 Workstream B.

Verifies that search tools using `response_format="content_and_artifact"`
emit `list[Document]` artifacts with stable `source_id`s for downstream
audit and citation-provenance enforcement.
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("langchain_core", reason="langchain-core not installed")

from langchain_core.documents import Document
from langchain_core.messages import ToolMessage

from src.pipeline.graph.tools import make_tools
from src.shared.case_state import CaseState


def _make_state(vector_store_id: str | None = "vs-test-123") -> dict:
    return {
        "case": CaseState(domain_vector_store_id=vector_store_id),
        "run_id": "run-1",
        "extra_instructions": {},
        "retry_counts": {},
        "halt": None,
        "is_resume": False,
        "start_agent": None,
    }


def _invoke_as_tool_call(tool, args: dict, call_id: str = "call-1") -> ToolMessage:
    """Invoke a LangChain tool with a ToolCall envelope so the artifact is preserved.

    Calling `tool.invoke({...})` directly returns content only; the LangChain
    contract for `response_format="content_and_artifact"` only surfaces the
    artifact when the invocation is shaped as a `ToolCall`.
    """
    return tool.invoke({"name": tool.name, "args": args, "id": call_id, "type": "tool_call"})


async def _ainvoke_as_tool_call(tool, args: dict, call_id: str = "call-1") -> ToolMessage:
    return await tool.ainvoke({"name": tool.name, "args": args, "id": call_id, "type": "tool_call"})


class TestSearchPrecedentsArtifact:
    @pytest.mark.asyncio
    async def test_returns_tool_message_with_document_artifact(self):
        state = _make_state(vector_store_id="vs-999")
        tools, _ = make_tools(state, "legal-knowledge")
        sp_tool = next(t for t in tools if t.name == "search_precedents")

        mock_result = MagicMock()
        mock_result.precedents = [
            {
                "citation": "Tan v Tan [2021] SGHC 1",
                "court": "High Court",
                "outcome": "",
                "reasoning_summary": "Rear-end collision; defendant liable.",
                "similarity_score": 0.91,
                "url": "https://example.gov.sg/cases/sghc-1",
                "source": "vector_store_fallback",
                "file_id": "file-abc123",
            }
        ]
        mock_result.metadata = {"source_failed": False, "pair_status": "ok"}

        with patch(
            "src.tools.search_precedents.search_precedents_with_meta",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            msg = await _ainvoke_as_tool_call(
                sp_tool, {"query": "rear-end", "domain": "small_claims"}
            )

        assert isinstance(msg, ToolMessage)
        assert isinstance(msg.content, str) and msg.content
        assert isinstance(msg.artifact, list)
        assert len(msg.artifact) == 1
        doc = msg.artifact[0]
        assert isinstance(doc, Document)
        assert doc.metadata["file_id"] == "file-abc123"
        assert doc.metadata["filename"] == "Tan v Tan [2021] SGHC 1"
        assert doc.metadata["score"] == pytest.approx(0.91)
        assert doc.metadata["source_id"].startswith("file-abc123:")
        # source_id format: <file_id>:<sha256(content)[:12]>
        prefix, content_hash = doc.metadata["source_id"].split(":", 1)
        assert prefix == "file-abc123"
        assert len(content_hash) == 12
        # Hash is deterministic over the document's content
        expected = hashlib.sha256(doc.page_content.encode("utf-8")).hexdigest()[:12]
        assert content_hash == expected

    @pytest.mark.asyncio
    async def test_source_id_stable_across_runs(self):
        state = _make_state(vector_store_id="vs-1")
        tools, _ = make_tools(state, "legal-knowledge")
        sp_tool = next(t for t in tools if t.name == "search_precedents")

        precedent = {
            "citation": "ABC v XYZ",
            "court": "CA",
            "outcome": "",
            "reasoning_summary": "Causation found.",
            "similarity_score": 0.7,
            "url": "https://example.gov.sg/cases/abc",
            "source": "vector_store_fallback",
            "file_id": "file-stable-1",
        }
        mock_result = MagicMock()
        mock_result.precedents = [precedent]
        mock_result.metadata = {"source_failed": False, "pair_status": "ok"}

        with patch(
            "src.tools.search_precedents.search_precedents_with_meta",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            first = await _ainvoke_as_tool_call(sp_tool, {"query": "q"}, "call-a")
            second = await _ainvoke_as_tool_call(sp_tool, {"query": "q"}, "call-b")

        assert first.artifact[0].metadata["source_id"] == second.artifact[0].metadata["source_id"]

    @pytest.mark.asyncio
    async def test_pair_result_synthesizes_file_id(self):
        """Live PAIR results have no file_id; tool must synthesize a stable surrogate."""
        state = _make_state(vector_store_id="vs-1")
        tools, _ = make_tools(state, "legal-knowledge")
        sp_tool = next(t for t in tools if t.name == "search_precedents")

        mock_result = MagicMock()
        mock_result.precedents = [
            {
                "citation": "Test v Test [2020] SGCA 5",
                "court": "Court of Appeal",
                "outcome": "",
                "reasoning_summary": "Some live PAIR snippet.",
                "similarity_score": 0.81,
                "url": "https://search.pair.gov.sg/case/sgca-5-2020",
                "source": "live_search",
            }
        ]
        mock_result.metadata = {"source_failed": False, "pair_status": "ok"}

        with patch(
            "src.tools.search_precedents.search_precedents_with_meta",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            msg = await _ainvoke_as_tool_call(sp_tool, {"query": "live"})

        doc = msg.artifact[0]
        assert doc.metadata["file_id"].startswith("pair:")
        assert ":" in doc.metadata["source_id"]
        assert doc.metadata["filename"] == "Test v Test [2020] SGCA 5"

    @pytest.mark.asyncio
    async def test_empty_results_yield_empty_artifact(self):
        state = _make_state(vector_store_id="vs-1")
        tools, _ = make_tools(state, "legal-knowledge")
        sp_tool = next(t for t in tools if t.name == "search_precedents")

        mock_result = MagicMock()
        mock_result.precedents = []
        mock_result.metadata = {"source_failed": True, "pair_status": "circuit_open"}

        with patch(
            "src.tools.search_precedents.search_precedents_with_meta",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            msg = await _ainvoke_as_tool_call(sp_tool, {"query": "nothing"})

        assert msg.artifact == []
        assert isinstance(msg.content, str)

    @pytest.mark.asyncio
    async def test_side_channel_still_records_metadata(self):
        """Wrapping the tool must not break the precedent_meta side-channel contract."""
        state = _make_state(vector_store_id="vs-1")
        tools, side_ch = make_tools(state, "legal-knowledge")
        sp_tool = next(t for t in tools if t.name == "search_precedents")

        mock_result = MagicMock()
        mock_result.precedents = []
        mock_result.metadata = {"source_failed": True, "pair_status": "circuit_open"}

        with patch(
            "src.tools.search_precedents.search_precedents_with_meta",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            await _ainvoke_as_tool_call(sp_tool, {"query": "x"})

        assert side_ch.metadata == {"source_failed": True, "pair_status": "circuit_open"}
