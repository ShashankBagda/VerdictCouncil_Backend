"""Unit tests for src.tools.cross_reference."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import openai
import pytest

from src.tools.cross_reference import cross_reference


def _make_chat_response(payload: dict):
    message = SimpleNamespace(content=json.dumps(payload))
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


@pytest.fixture
def _openai_client():
    client = AsyncMock(spec=openai.AsyncOpenAI)
    client.chat = AsyncMock()
    client.chat.completions = AsyncMock()
    return client


def _two_segments():
    return [
        {
            "doc_id": "doc-1",
            "filename": "claim.pdf",
            "text": "Plaintiff claims $5000.",
            "page": 1,
            "paragraph": 1,
        },
        {
            "doc_id": "doc-2",
            "filename": "response.pdf",
            "text": "Defendant disputes $5000.",
            "page": 1,
            "paragraph": 1,
        },
    ]


# ------------------------------------------------------------------ #
# Contradictions found
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_contradictions_found(_openai_client):
    client = _openai_client
    api_payload = {
        "contradictions": [
            {
                "doc_a": "doc-1",
                "doc_b": "doc-2",
                "description": "Amount disputed",
                "severity": "critical",
            }
        ],
        "corroborations": [],
    }
    client.chat.completions.create = AsyncMock(return_value=_make_chat_response(api_payload))

    with patch("src.tools.cross_reference.openai.AsyncOpenAI", return_value=client):
        result = await cross_reference(_two_segments(), "all")

    assert len(result["contradictions"]) == 1
    assert result["contradictions"][0]["severity"] == "critical"
    assert result["corroborations"] == []


# ------------------------------------------------------------------ #
# Corroborations found
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_corroborations_found(_openai_client):
    client = _openai_client
    api_payload = {
        "contradictions": [],
        "corroborations": [
            {
                "doc_ids": ["doc-1", "doc-2"],
                "description": "Both mention incident on 2026-01-15",
                "strength": "strong",
            }
        ],
    }
    client.chat.completions.create = AsyncMock(return_value=_make_chat_response(api_payload))

    with patch("src.tools.cross_reference.openai.AsyncOpenAI", return_value=client):
        result = await cross_reference(_two_segments(), "all")

    assert len(result["corroborations"]) == 1
    assert result["corroborations"][0]["strength"] == "strong"


# ------------------------------------------------------------------ #
# Empty input (fewer than 2 documents)
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_empty_document_list_returns_empty():
    result = await cross_reference([], "all")
    assert result == {"contradictions": [], "corroborations": []}


@pytest.mark.asyncio
async def test_single_document_returns_empty():
    result = await cross_reference(
        [{"doc_id": "doc-1", "text": "Only one doc", "page": 1, "paragraph": 1}],
        "all",
    )
    assert result == {"contradictions": [], "corroborations": []}
