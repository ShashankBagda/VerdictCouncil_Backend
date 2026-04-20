"""Unit tests for SAM tool wrappers."""

from unittest.mock import AsyncMock, patch

import pytest

from src.tools.sam.search_precedents_tool import (
    SEARCH_PRECEDENTS_SCHEMA,
    SearchPrecedentsTool,
)
from src.tools.search_precedents import SearchResult


class TestSearchPrecedentsTool:
    """Tests for SearchPrecedentsTool."""

    def test_instantiation(self):
        tool = SearchPrecedentsTool()
        assert tool is not None

    def test_tool_name(self):
        tool = SearchPrecedentsTool()
        assert tool.tool_name == "search_precedents"

    def test_tool_description(self):
        tool = SearchPrecedentsTool()
        assert "PAIR Search API" in tool.tool_description

    def test_parameters_schema_structure(self):
        tool = SearchPrecedentsTool()
        schema = tool.parameters_schema
        assert schema["type"] == "OBJECT"
        assert "query" in schema["properties"]
        assert "domain" in schema["properties"]
        assert "max_results" in schema["properties"]

    def test_parameters_schema_required_fields(self):
        tool = SearchPrecedentsTool()
        schema = tool.parameters_schema
        assert "query" in schema["required"]
        assert "domain" in schema["required"]
        # max_results is optional
        assert "max_results" not in schema["required"]

    def test_parameters_schema_types(self):
        tool = SearchPrecedentsTool()
        schema = tool.parameters_schema
        assert schema["properties"]["query"]["type"] == "STRING"
        assert schema["properties"]["domain"]["type"] == "STRING"
        assert schema["properties"]["max_results"]["type"] == "INTEGER"

    @pytest.mark.asyncio
    async def test_init_noop(self):
        tool = SearchPrecedentsTool()
        # Should not raise
        await tool.init()

    @pytest.mark.asyncio
    async def test_cleanup_noop(self):
        tool = SearchPrecedentsTool()
        # Should not raise
        await tool.cleanup()

    @pytest.mark.asyncio
    async def test_run_async_impl_delegates(self):
        tool = SearchPrecedentsTool()
        mock_results = [{"citation": "SGHC 123", "similarity_score": 0.95}]
        mock_search_result = SearchResult(
            precedents=mock_results,
            metadata={"source_failed": False, "fallback_used": False, "pair_status": "ok"},
        )

        with patch(
            "src.tools.search_precedents.search_precedents_with_meta",
            new_callable=AsyncMock,
            return_value=mock_search_result,
        ):
            result = await tool._run_async_impl(
                args={
                    "query": "sale of goods satisfactory quality",
                    "domain": "small_claims",
                    "max_results": 5,
                }
            )

        assert result == mock_results

    @pytest.mark.asyncio
    async def test_run_async_impl_passes_args(self):
        tool = SearchPrecedentsTool()
        expected_args = {
            "query": "road traffic act speeding",
            "domain": "traffic",
            "max_results": 3,
        }

        with patch(
            "src.tools.search_precedents.search_precedents_with_meta",
            new_callable=AsyncMock,
            return_value=SearchResult(precedents=[]),
        ) as mock_fn:
            await tool._run_async_impl(args=expected_args)
            mock_fn.assert_called_once_with(**expected_args)


class TestSearchPrecedentsSchema:
    """Tests for the standalone schema constant."""

    def test_schema_is_dict(self):
        assert isinstance(SEARCH_PRECEDENTS_SCHEMA, dict)

    def test_schema_matches_runner_tool_schemas(self):
        """Verify schema covers the same fields as runner.TOOL_SCHEMAS."""
        from src.pipeline.runner import TOOL_SCHEMAS

        runner_params = TOOL_SCHEMAS["search_precedents"]["function"]["parameters"]
        runner_props = set(runner_params["properties"].keys())
        sam_props = set(SEARCH_PRECEDENTS_SCHEMA["properties"].keys())
        assert runner_props == sam_props
