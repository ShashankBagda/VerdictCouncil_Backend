"""Unit tests for the parse_document cache short-circuit in make_tools.

The parse_document_tool closure checks CaseState.raw_documents for a matching
openai_file_id with non-empty parsed_text before calling the OpenAI API.
These tests exercise that path through the tool closure, not the underlying
parse_document function directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("langchain_core", reason="langchain-core not installed")
from src.pipeline.graph.tools import make_tools
from src.shared.case_state import CaseState
from src.shared.sanitization import SanitizationResult

_FILE_ID = "file-test-abc123"
_PARSED_TEXT = "This is the pre-parsed document text."
_PAGES = [{"page_number": 1, "text": _PARSED_TEXT, "tables": []}]


def _make_state(raw_documents: list[dict]) -> dict:
    return {
        "case": CaseState(raw_documents=raw_documents),
        "run_id": "run-cache-test",
        "extra_instructions": {},
        "retry_counts": {},
        "halt": None,
        "is_resume": False,
        "start_agent": None,
    }


def _get_parse_tool(raw_documents: list[dict]):
    """Build state, call make_tools, return the parse_document tool closure."""
    state = _make_state(raw_documents)
    tools, _ = make_tools(state)
    return next(t for t in tools if t.name == "parse_document")


# ---------------------------------------------------------------------------
# Cache hit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_skips_api_call():
    """Hydrated raw_documents entry → return cached dict, no OpenAI call."""
    raw_documents = [
        {
            "openai_file_id": _FILE_ID,
            "filename": "statement.pdf",
            "parsed_text": _PARSED_TEXT,
            "pages": _PAGES,
        }
    ]
    tool = _get_parse_tool(raw_documents)

    with patch("src.tools.parse_document.parse_document", new_callable=AsyncMock) as mock_parse:
        result = await tool.ainvoke({"file_id": _FILE_ID})

    mock_parse.assert_not_called()
    assert result["file_id"] == _FILE_ID
    assert result["filename"] == "statement.pdf"
    assert result["text"] == _PARSED_TEXT
    assert result["pages"] == _PAGES
    assert "Served from CaseState cache" in result["parsing_notes"][0]
    assert isinstance(result["sanitization"], SanitizationResult)
    assert result["sanitization"].chunks_scanned == len(_PAGES)


@pytest.mark.asyncio
async def test_cache_hit_extracts_tables_from_pages():
    """Tables list is derived from the pages in the cached entry."""
    pages_with_tables = [
        {"page_number": 1, "text": "page 1", "tables": [["col1", "col2"], ["a", "b"]]},
        {"page_number": 2, "text": "page 2", "tables": []},
    ]
    raw_documents = [
        {
            "openai_file_id": _FILE_ID,
            "filename": "contract.pdf",
            "parsed_text": "page 1\npage 2",
            "pages": pages_with_tables,
        }
    ]
    tool = _get_parse_tool(raw_documents)

    with patch("src.tools.parse_document.parse_document", new_callable=AsyncMock):
        result = await tool.ainvoke({"file_id": _FILE_ID})

    assert result["tables"] == [["col1", "col2"], ["a", "b"]]


@pytest.mark.asyncio
async def test_cache_hit_synthesises_pages_when_none():
    """Matching entry with parsed_text but pages=None → single synthesised page."""
    raw_documents = [
        {
            "openai_file_id": _FILE_ID,
            "filename": "exhibit.pdf",
            "parsed_text": _PARSED_TEXT,
            "pages": None,
        }
    ]
    tool = _get_parse_tool(raw_documents)

    with patch("src.tools.parse_document.parse_document", new_callable=AsyncMock) as mock_parse:
        result = await tool.ainvoke({"file_id": _FILE_ID})

    mock_parse.assert_not_called()
    assert len(result["pages"]) == 1
    assert result["pages"][0]["page_number"] == 1
    assert result["pages"][0]["text"] == _PARSED_TEXT


@pytest.mark.asyncio
async def test_cache_hit_picks_correct_entry_among_multiple():
    """Only the entry matching file_id is used; other entries are ignored."""
    raw_documents = [
        {"openai_file_id": "file-other-1", "filename": "other1.pdf", "parsed_text": "other"},
        {
            "openai_file_id": _FILE_ID,
            "filename": "target.pdf",
            "parsed_text": _PARSED_TEXT,
            "pages": _PAGES,
        },
        {"openai_file_id": "file-other-2", "filename": "other2.pdf", "parsed_text": "other2"},
    ]
    tool = _get_parse_tool(raw_documents)

    with patch("src.tools.parse_document.parse_document", new_callable=AsyncMock) as mock_parse:
        result = await tool.ainvoke({"file_id": _FILE_ID})

    mock_parse.assert_not_called()
    assert result["filename"] == "target.pdf"
    assert result["text"] == _PARSED_TEXT


# ---------------------------------------------------------------------------
# Cache miss — fall through to API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_miss_empty_parsed_text_calls_api():
    """Entry exists but parsed_text is empty → fall through to OpenAI."""
    raw_documents = [
        {
            "openai_file_id": _FILE_ID,
            "filename": "not-yet-parsed.pdf",
            "parsed_text": "",
            "pages": None,
        }
    ]
    tool = _get_parse_tool(raw_documents)

    api_result = {"file_id": _FILE_ID, "text": "fresh from api", "pages": [], "tables": []}
    with patch(
        "src.tools.parse_document.parse_document",
        new_callable=AsyncMock,
        return_value=api_result,
    ) as mock_parse:
        result = await tool.ainvoke({"file_id": _FILE_ID})

    mock_parse.assert_called_once_with(file_id=_FILE_ID, extract_tables=True, ocr_enabled=False)
    assert result["text"] == "fresh from api"


@pytest.mark.asyncio
async def test_cache_miss_file_id_absent_calls_api():
    """No entry in raw_documents matching the file_id → fall through to API."""
    raw_documents = [
        {
            "openai_file_id": "file-completely-different",
            "filename": "other.pdf",
            "parsed_text": "irrelevant",
            "pages": _PAGES,
        }
    ]
    tool = _get_parse_tool(raw_documents)

    api_result = {"file_id": _FILE_ID, "text": "api text", "pages": [], "tables": []}
    with patch(
        "src.tools.parse_document.parse_document",
        new_callable=AsyncMock,
        return_value=api_result,
    ) as mock_parse:
        result = await tool.ainvoke({"file_id": _FILE_ID})

    mock_parse.assert_called_once()
    assert result["text"] == "api text"


@pytest.mark.asyncio
async def test_cache_miss_empty_raw_documents_calls_api():
    """No raw_documents at all → fall through to API."""
    tool = _get_parse_tool([])

    api_result = {"file_id": _FILE_ID, "text": "api text", "pages": [], "tables": []}
    with patch(
        "src.tools.parse_document.parse_document",
        new_callable=AsyncMock,
        return_value=api_result,
    ) as mock_parse:
        result = await tool.ainvoke({"file_id": _FILE_ID})

    mock_parse.assert_called_once()
    assert result["text"] == "api text"
