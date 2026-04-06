"""Unit tests for src.tools.vector_store_fallback."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.vector_store_fallback import VectorStoreError, vector_store_search


def _mock_file_search_result(filename="case_001.pdf", score=0.85, text="The court held..."):
    """Build a mock file search result matching OpenAI SDK structure."""
    result = MagicMock()
    result.filename = filename
    result.score = score
    result.text = text
    return result


def _mock_file_search_call(results=None):
    """Build a mock file_search_call output item."""
    item = MagicMock()
    item.type = "file_search_call"
    item.results = results or []
    return item


def _mock_response(output_items=None):
    """Build a mock OpenAI responses.create response."""
    resp = MagicMock()
    resp.output = output_items or []
    return resp


# ------------------------------------------------------------------ #
# Happy path: returns formatted results
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_happy_path_returns_results():
    results = [
        _mock_file_search_result("case_001.pdf", 0.92, "Court held that deposit must be refunded"),
        _mock_file_search_result("case_002.pdf", 0.78, "On appeal, the court found"),
    ]
    search_call = _mock_file_search_call(results)
    response = _mock_response([search_call])

    mock_client = AsyncMock()
    mock_client.responses.create = AsyncMock(return_value=response)

    with (
        patch("src.tools.vector_store_fallback.settings") as mock_settings,
        patch("src.tools.vector_store_fallback.AsyncOpenAI", return_value=mock_client),
    ):
        mock_settings.openai_vector_store_id = "vs_test123"
        mock_settings.openai_api_key = "test-key"
        mock_settings.openai_model_lightweight = "gpt-4o-mini"

        output = await vector_store_search("breach of contract")

    assert len(output) == 2
    assert output[0]["citation"] == "case_001.pdf"
    assert output[0]["similarity_score"] == 0.92
    assert output[0]["source"] == "vector_store_fallback"
    assert output[1]["citation"] == "case_002.pdf"


# ------------------------------------------------------------------ #
# Results tagged with source: "vector_store_fallback"
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_results_tagged_with_source():
    results = [_mock_file_search_result()]
    search_call = _mock_file_search_call(results)
    response = _mock_response([search_call])

    mock_client = AsyncMock()
    mock_client.responses.create = AsyncMock(return_value=response)

    with (
        patch("src.tools.vector_store_fallback.settings") as mock_settings,
        patch("src.tools.vector_store_fallback.AsyncOpenAI", return_value=mock_client),
    ):
        mock_settings.openai_vector_store_id = "vs_test123"
        mock_settings.openai_api_key = "test-key"
        mock_settings.openai_model_lightweight = "gpt-4o-mini"

        output = await vector_store_search("test query")

    for item in output:
        assert item["source"] == "vector_store_fallback"


# ------------------------------------------------------------------ #
# Empty results from vector store
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_empty_results():
    search_call = _mock_file_search_call([])
    response = _mock_response([search_call])

    mock_client = AsyncMock()
    mock_client.responses.create = AsyncMock(return_value=response)

    with (
        patch("src.tools.vector_store_fallback.settings") as mock_settings,
        patch("src.tools.vector_store_fallback.AsyncOpenAI", return_value=mock_client),
    ):
        mock_settings.openai_vector_store_id = "vs_test123"
        mock_settings.openai_api_key = "test-key"
        mock_settings.openai_model_lightweight = "gpt-4o-mini"

        output = await vector_store_search("obscure query")

    assert output == []


# ------------------------------------------------------------------ #
# Unconfigured vector store ID raises VectorStoreError
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_unconfigured_vector_store_id_raises():
    with patch("src.tools.vector_store_fallback.settings") as mock_settings:
        mock_settings.openai_vector_store_id = ""

        with pytest.raises(VectorStoreError, match="not configured"):
            await vector_store_search("any query")


# ------------------------------------------------------------------ #
# API error raises VectorStoreError
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_api_error_raises_vector_store_error():
    mock_client = AsyncMock()
    mock_client.responses.create = AsyncMock(side_effect=Exception("API error"))

    with (
        patch("src.tools.vector_store_fallback.settings") as mock_settings,
        patch("src.tools.vector_store_fallback.AsyncOpenAI", return_value=mock_client),
    ):
        mock_settings.openai_vector_store_id = "vs_test123"
        mock_settings.openai_api_key = "test-key"
        mock_settings.openai_model_lightweight = "gpt-4o-mini"

        with pytest.raises(VectorStoreError, match="API error"):
            await vector_store_search("error query")
