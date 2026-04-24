"""Unit tests for domain-RAG-specific semantics in src.tools.vector_store_fallback.

Covers:
- Fail-closed: no vector_store_id + allow_global_fallback=False raises VectorStoreError
- Global fallback: no vector_store_id + allow_global_fallback=True uses global store
- Direct use: explicit vector_store_id is forwarded to the OpenAI call
- No global configured: allow_global_fallback=True but settings empty raises VectorStoreError
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.vector_store_fallback import VectorStoreError, vector_store_search


def _mock_file_search_result(filename="case_001.pdf", score=0.85, text="The court held..."):
    result = MagicMock()
    result.filename = filename
    result.score = score
    result.text = text
    return result


def _mock_file_search_call(results=None):
    item = MagicMock()
    item.type = "file_search_call"
    item.results = results or []
    return item


def _mock_response(output_items=None):
    resp = MagicMock()
    resp.output = output_items or []
    return resp


# ------------------------------------------------------------------ #
# Fail-closed: no store id + allow_global_fallback=False raises error
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_fail_closed_no_vector_store_id_raises():
    """vector_store_id=None + allow_global_fallback=False raises VectorStoreError immediately."""
    with patch("src.tools.vector_store_fallback.settings") as mock_settings:
        mock_settings.openai_vector_store_id = "vs_global_configured"

        with pytest.raises(VectorStoreError):
            await vector_store_search(
                "test query",
                vector_store_id=None,
                allow_global_fallback=False,
            )


# ------------------------------------------------------------------ #
# Global fallback: allow_global_fallback=True uses global store
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_global_fallback_uses_global_store_when_configured():
    """vector_store_id=None + allow_global_fallback=True routes to global store id."""
    results = [_mock_file_search_result("global_case.pdf", 0.80, "Global store result")]
    search_call = _mock_file_search_call(results)
    response = _mock_response([search_call])

    mock_client = AsyncMock()
    mock_client.responses.create = AsyncMock(return_value=response)

    with (
        patch("src.tools.vector_store_fallback.settings") as mock_settings,
        patch("src.tools.vector_store_fallback.AsyncOpenAI", return_value=mock_client),
    ):
        mock_settings.openai_vector_store_id = "vs_global_store"
        mock_settings.openai_api_key = "test-key"
        mock_settings.openai_model_lightweight = "gpt-4o-mini"

        output = await vector_store_search(
            "test query",
            vector_store_id=None,
            allow_global_fallback=True,
        )

    assert len(output) == 1
    assert output[0]["citation"] == "global_case.pdf"
    # Confirm that the OpenAI call used the global vector store id
    call_kwargs = mock_client.responses.create.call_args
    tools_arg = call_kwargs.kwargs.get("tools") or call_kwargs[1].get("tools") or call_kwargs[0][2]
    assert any("vs_global_store" in str(t) for t in tools_arg)


# ------------------------------------------------------------------ #
# Direct use: explicit vector_store_id is forwarded
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_direct_vector_store_id_is_used():
    """Providing vector_store_id explicitly calls OpenAI with that id."""
    results = [_mock_file_search_result("domain_case.pdf", 0.90, "Domain-specific result")]
    search_call = _mock_file_search_call(results)
    response = _mock_response([search_call])

    mock_client = AsyncMock()
    mock_client.responses.create = AsyncMock(return_value=response)

    with (
        patch("src.tools.vector_store_fallback.settings") as mock_settings,
        patch("src.tools.vector_store_fallback.AsyncOpenAI", return_value=mock_client),
    ):
        mock_settings.openai_vector_store_id = "vs_global_should_not_be_used"
        mock_settings.openai_api_key = "test-key"
        mock_settings.openai_model_lightweight = "gpt-4o-mini"

        output = await vector_store_search(
            "domain query",
            vector_store_id="vs_test",
        )

    assert len(output) == 1
    assert output[0]["citation"] == "domain_case.pdf"
    # Confirm that the provided domain store id was forwarded, not the global one
    call_kwargs = mock_client.responses.create.call_args
    tools_arg = call_kwargs.kwargs.get("tools") or call_kwargs[1].get("tools") or call_kwargs[0][2]
    assert any("vs_test" in str(t) for t in tools_arg)
    assert not any("vs_global_should_not_be_used" in str(t) for t in tools_arg)


# ------------------------------------------------------------------ #
# No global store configured + allow_global_fallback=True raises
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_global_fallback_with_no_global_configured_raises():
    """allow_global_fallback=True but settings.openai_vector_store_id is empty raises VectorStoreError."""
    with patch("src.tools.vector_store_fallback.settings") as mock_settings:
        mock_settings.openai_vector_store_id = ""

        with pytest.raises(VectorStoreError):
            await vector_store_search(
                "test query",
                vector_store_id=None,
                allow_global_fallback=True,
            )


# ------------------------------------------------------------------ #
# Direct use returns correct results (no fallback involved)
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_direct_vector_store_id_returns_tagged_results():
    """Results from a directly-provided vector_store_id are tagged with vector_store_fallback source."""
    results = [
        _mock_file_search_result("case_a.pdf", 0.88, "Court held that..."),
        _mock_file_search_result("case_b.pdf", 0.70, "On appeal..."),
    ]
    search_call = _mock_file_search_call(results)
    response = _mock_response([search_call])

    mock_client = AsyncMock()
    mock_client.responses.create = AsyncMock(return_value=response)

    with (
        patch("src.tools.vector_store_fallback.settings") as mock_settings,
        patch("src.tools.vector_store_fallback.AsyncOpenAI", return_value=mock_client),
    ):
        mock_settings.openai_api_key = "test-key"
        mock_settings.openai_model_lightweight = "gpt-4o-mini"

        output = await vector_store_search("query", vector_store_id="vs_test")

    assert len(output) == 2
    for item in output:
        assert item["source"] == "vector_store_fallback"
