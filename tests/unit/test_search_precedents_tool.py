"""Unit tests for the SAM DynamicTool wrapper around search_precedents.

These tests verify that the wrapper:
- Returns the precedent list (preserving the agent-visible contract).
- Writes precedent source metadata into ``tool_context.state`` so the
  SAM orchestrator can propagate it to ``CaseState.precedent_source_metadata``.
- Performs the worst-of merge across multiple search calls within a
  single session, matching :class:`PipelineRunner` behavior.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.sam.search_precedents_tool import (
    PRECEDENT_META_STATE_KEY,
    SearchPrecedentsTool,
    _merge_precedent_meta,
)
from src.tools.search_precedents import SearchResult


def _make_tool_context() -> SimpleNamespace:
    """Build a minimal stand-in for the SAM ToolContext (state-only)."""
    return SimpleNamespace(state={})


@pytest.mark.asyncio
async def test_run_returns_precedent_list_only() -> None:
    """Agents see a plain list of precedents, not the SearchResult wrapper."""
    tool = SearchPrecedentsTool()
    fake_result = SearchResult(
        precedents=[{"citation": "[2025] SGHC 1"}],
        metadata={"source_failed": False, "fallback_used": False, "pair_status": "ok"},
    )

    with patch(
        "src.tools.search_precedents.search_precedents_with_meta",
        AsyncMock(return_value=fake_result),
    ):
        out = await tool._run_async_impl(
            args={"query": "q", "domain": "small_claims"},
            tool_context=_make_tool_context(),
        )

    assert out == [{"citation": "[2025] SGHC 1"}]


@pytest.mark.asyncio
async def test_metadata_written_to_tool_context_state() -> None:
    """source_failed metadata is exposed via tool_context.state."""
    tool = SearchPrecedentsTool()
    failed_result = SearchResult(
        precedents=[],
        metadata={
            "source_failed": True,
            "fallback_used": True,
            "pair_status": "circuit_open",
        },
    )
    ctx = _make_tool_context()

    with patch(
        "src.tools.search_precedents.search_precedents_with_meta",
        AsyncMock(return_value=failed_result),
    ):
        await tool._run_async_impl(
            args={"query": "q", "domain": "small_claims"},
            tool_context=ctx,
        )

    assert ctx.state[PRECEDENT_META_STATE_KEY]["source_failed"] is True
    assert ctx.state[PRECEDENT_META_STATE_KEY]["pair_status"] == "circuit_open"


@pytest.mark.asyncio
async def test_metadata_merge_preserves_failure_across_calls() -> None:
    """A later failure escalates source_failed even if the first call succeeded."""
    tool = SearchPrecedentsTool()
    ctx = _make_tool_context()

    ok_result = SearchResult(
        precedents=[{"citation": "[2024] SGCA 9"}],
        metadata={"source_failed": False, "fallback_used": False, "pair_status": "ok"},
    )
    fail_result = SearchResult(
        precedents=[],
        metadata={
            "source_failed": True,
            "fallback_used": True,
            "pair_status": "failed (open)",
        },
    )

    with patch(
        "src.tools.search_precedents.search_precedents_with_meta",
        AsyncMock(side_effect=[ok_result, fail_result]),
    ):
        await tool._run_async_impl(
            args={"query": "q1", "domain": "small_claims"},
            tool_context=ctx,
        )
        await tool._run_async_impl(
            args={"query": "q2", "domain": "small_claims"},
            tool_context=ctx,
        )

    merged = ctx.state[PRECEDENT_META_STATE_KEY]
    assert merged["source_failed"] is True
    assert merged["pair_status"] == "failed (open)"


@pytest.mark.asyncio
async def test_first_success_metadata_stored_verbatim() -> None:
    """The first call's full metadata is stored when no prior entry exists."""
    tool = SearchPrecedentsTool()
    ctx = _make_tool_context()
    ok_result = SearchResult(
        precedents=[{"citation": "[2025] SGHC 7"}],
        metadata={"source_failed": False, "fallback_used": True, "pair_status": "ok"},
    )

    with patch(
        "src.tools.search_precedents.search_precedents_with_meta",
        AsyncMock(return_value=ok_result),
    ):
        await tool._run_async_impl(
            args={"query": "q", "domain": "traffic"},
            tool_context=ctx,
        )

    stored = ctx.state[PRECEDENT_META_STATE_KEY]
    assert stored["source_failed"] is False
    assert stored["fallback_used"] is True
    assert stored["pair_status"] == "ok"


@pytest.mark.asyncio
async def test_missing_tool_context_is_tolerated() -> None:
    """Direct calls without a tool_context still return precedents."""
    tool = SearchPrecedentsTool()
    fake_result = SearchResult(
        precedents=[{"citation": "[2025] SGHC 11"}],
        metadata={"source_failed": False, "fallback_used": False, "pair_status": "ok"},
    )

    with patch(
        "src.tools.search_precedents.search_precedents_with_meta",
        AsyncMock(return_value=fake_result),
    ):
        out = await tool._run_async_impl(
            args={"query": "q", "domain": "small_claims"},
            tool_context=None,
        )

    assert out == [{"citation": "[2025] SGHC 11"}]


def test_merge_helper_first_call_stores_copy() -> None:
    """The merge helper does not mutate the caller's incoming dict."""
    incoming = {"source_failed": False, "fallback_used": False, "pair_status": "ok"}
    merged = _merge_precedent_meta(None, incoming)
    merged["source_failed"] = True
    assert incoming["source_failed"] is False


def test_merge_helper_success_after_failure_does_not_clear() -> None:
    """Once source_failed is True it stays True even if a later call succeeded."""
    existing = {
        "source_failed": True,
        "fallback_used": True,
        "pair_status": "circuit_open",
    }
    incoming = {"source_failed": False, "fallback_used": False, "pair_status": "ok"}
    merged = _merge_precedent_meta(existing, incoming)
    assert merged["source_failed"] is True
    assert merged["pair_status"] == "circuit_open"
