"""Unit tests for src.tools.search_domain_guidance.

Covers:
- Fail-closed on null/empty vector_store_id (raises DomainGuidanceUnavailable)
- Successful search returns structured results
- OpenAI API error is wrapped in DomainGuidanceUnavailable
- Cross-domain sentinel scoping: domain A's store id retrieves domain A content
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.exceptions import DomainGuidanceUnavailable
from src.tools.search_domain_guidance import search_domain_guidance


def _mock_file_search_result(filename="statute.pdf", score=0.90, text="Section 14 SOGA..."):
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


def _mock_openai_client(response=None):
    client = AsyncMock()
    client.responses = MagicMock(create=AsyncMock(return_value=response or _mock_response()))
    return client


# ---------------------------------------------------------------------------
# Fail-closed: null/empty vector_store_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_domain_guidance_raises_on_empty_vector_store_id():
    """Empty string vector_store_id must raise DomainGuidanceUnavailable immediately."""
    with pytest.raises(DomainGuidanceUnavailable):
        await search_domain_guidance("SOGA section 14", vector_store_id="")


@pytest.mark.asyncio
async def test_search_domain_guidance_raises_on_none_vector_store_id():
    """None vector_store_id must raise DomainGuidanceUnavailable immediately."""
    with pytest.raises(DomainGuidanceUnavailable):
        await search_domain_guidance("road traffic act", vector_store_id=None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_fail_closed_does_not_call_openai():
    """When vector_store_id is empty, OpenAI must not be called — no network on fail-closed."""
    mock_client = _mock_openai_client()
    with patch("src.tools.search_domain_guidance.AsyncOpenAI", return_value=mock_client):
        with pytest.raises(DomainGuidanceUnavailable):
            await search_domain_guidance("query", vector_store_id="")
    mock_client.responses.create.assert_not_awaited()


# ---------------------------------------------------------------------------
# Happy path: results are structured correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_domain_guidance_returns_structured_results():
    """Successful search returns a list of dicts with citation, content, score, source."""
    results = [
        _mock_file_search_result("soga_s14.pdf", 0.95, "Satisfactory quality means..."),
        _mock_file_search_result("cpfta_unfair.pdf", 0.80, "Unfair practice means..."),
    ]
    search_call = _mock_file_search_call(results)
    response = _mock_response([search_call])
    mock_client = _mock_openai_client(response)

    with (
        patch("src.tools.search_domain_guidance.AsyncOpenAI", return_value=mock_client),
        patch("src.tools.search_domain_guidance.settings") as mock_settings,
    ):
        mock_settings.openai_api_key = "test-key"
        mock_settings.openai_model_lightweight = "gpt-4o-mini"
        output = await search_domain_guidance("SOGA satisfactory quality", vector_store_id="vs_sct")

    assert len(output) == 2
    assert output[0]["citation"] == "soga_s14.pdf"
    assert output[0]["source"] == "domain_guidance"
    assert "score" in output[0]
    assert "content" in output[0]


@pytest.mark.asyncio
async def test_search_domain_guidance_uses_provided_vector_store_id():
    """The provided vector_store_id must appear in the OpenAI API call."""
    response = _mock_response([_mock_file_search_call()])
    mock_client = _mock_openai_client(response)

    with (
        patch("src.tools.search_domain_guidance.AsyncOpenAI", return_value=mock_client),
        patch("src.tools.search_domain_guidance.settings") as mock_settings,
    ):
        mock_settings.openai_api_key = "test-key"
        mock_settings.openai_model_lightweight = "gpt-4o-mini"
        await search_domain_guidance("test query", vector_store_id="vs_domain_a")

    call_kwargs = mock_client.responses.create.await_args
    tools_arg = (
        call_kwargs.kwargs.get("tools")
        or call_kwargs[1].get("tools")
        or call_kwargs[0][2]
    )
    assert any("vs_domain_a" in str(t) for t in tools_arg), (
        "vector_store_id vs_domain_a must be forwarded to the OpenAI call"
    )


# ---------------------------------------------------------------------------
# Cross-domain sentinel scoping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_domain_a_store_id_does_not_retrieve_domain_b_sentinel():
    """Calling with domain A's store id must NOT use domain B's store id in the OpenAI call.

    This verifies that the tool passes the caller-supplied id verbatim and does not
    substitute or fall back to any other store id.
    """
    response = _mock_response([_mock_file_search_call()])
    mock_client = _mock_openai_client(response)
    domain_a_store = "vs_domain_a_store"
    domain_b_store = "vs_domain_b_store"

    with (
        patch("src.tools.search_domain_guidance.AsyncOpenAI", return_value=mock_client),
        patch("src.tools.search_domain_guidance.settings") as mock_settings,
    ):
        mock_settings.openai_api_key = "test-key"
        mock_settings.openai_model_lightweight = "gpt-4o-mini"
        await search_domain_guidance("query", vector_store_id=domain_a_store)

    call_kwargs = mock_client.responses.create.await_args
    tools_arg = (
        call_kwargs.kwargs.get("tools")
        or call_kwargs[1].get("tools")
        or call_kwargs[0][2]
    )
    assert any(domain_a_store in str(t) for t in tools_arg)
    assert not any(domain_b_store in str(t) for t in tools_arg), (
        "Domain B's store id must not appear when domain A was requested"
    )


# ---------------------------------------------------------------------------
# OpenAI API error → DomainGuidanceUnavailable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_api_error_raises_domain_guidance_unavailable():
    """An OpenAI error during search raises DomainGuidanceUnavailable (CriticalToolFailure)."""
    import openai

    mock_client = AsyncMock()
    mock_client.responses = MagicMock(
        create=AsyncMock(side_effect=openai.APIConnectionError(request=MagicMock()))
    )

    with (
        patch("src.tools.search_domain_guidance.AsyncOpenAI", return_value=mock_client),
        patch("src.tools.search_domain_guidance.settings") as mock_settings,
    ):
        mock_settings.openai_api_key = "test-key"
        mock_settings.openai_model_lightweight = "gpt-4o-mini"
        with pytest.raises(DomainGuidanceUnavailable):
            await search_domain_guidance("query", vector_store_id="vs_test")


# ---------------------------------------------------------------------------
# max_results is respected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_domain_guidance_respects_max_results():
    """Results are capped at max_results even if OpenAI returns more."""
    results = [_mock_file_search_result(f"doc_{i}.pdf", 0.9 - i * 0.1, f"Text {i}") for i in range(10)]
    search_call = _mock_file_search_call(results)
    response = _mock_response([search_call])
    mock_client = _mock_openai_client(response)

    with (
        patch("src.tools.search_domain_guidance.AsyncOpenAI", return_value=mock_client),
        patch("src.tools.search_domain_guidance.settings") as mock_settings,
    ):
        mock_settings.openai_api_key = "test-key"
        mock_settings.openai_model_lightweight = "gpt-4o-mini"
        output = await search_domain_guidance("query", vector_store_id="vs_test", max_results=3)

    assert len(output) <= 3
