"""Unit tests for src.tools.generate_questions."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import openai
import pytest

from src.tools.generate_questions import generate_questions


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


def _witnesses_with_gaps():
    return [
        {
            "name": "Alice Tan",
            "credibility_score": 45,
            "testimony_summary": "Claims she saw the accident from across the street.",
            "weaknesses": ["Poor viewing angle", "Delayed report"],
        }
    ]


def _witnesses_no_gaps():
    return [
        {
            "name": "Bob Lee",
            "credibility_score": 92,
            "testimony_summary": "Provided CCTV footage from his shop.",
            "weaknesses": [],
        }
    ]


_EVIDENCE = {"items": [{"id": "e1", "description": "CCTV footage", "strength": "strong"}]}
_FACTS = {"facts": [{"fact_id": "f1", "event": "Collision at junction"}]}


# ------------------------------------------------------------------ #
# Witnesses with credibility gaps produce questions
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_witnesses_with_gaps_produce_questions(_openai_client):
    client = _openai_client
    api_payload = {
        "witnesses": [
            {
                "witness_name": "Alice Tan",
                "questions": [
                    {
                        "question": "Can you describe your exact position?",
                        "rationale": "Viewing angle is questionable.",
                        "targets_weakness": "Poor viewing angle",
                    },
                    {
                        "question": "Why did you wait 3 days to report?",
                        "rationale": "Delay undermines reliability.",
                        "targets_weakness": "Delayed report",
                    },
                ],
            }
        ]
    }
    client.chat.completions.create = AsyncMock(return_value=_make_chat_response(api_payload))

    with patch("src.tools.generate_questions.openai.AsyncOpenAI", return_value=client):
        result = await generate_questions(_witnesses_with_gaps(), _EVIDENCE, _FACTS)

    assert len(result) == 1
    assert result[0]["witness_name"] == "Alice Tan"
    assert len(result[0]["questions"]) == 2
    assert result[0]["questions"][0]["targets_weakness"] == "Poor viewing angle"


# ------------------------------------------------------------------ #
# No gaps -> minimal questions
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_no_gaps_produces_minimal_questions(_openai_client):
    client = _openai_client
    api_payload = {
        "witnesses": [
            {
                "witness_name": "Bob Lee",
                "questions": [],
            }
        ]
    }
    client.chat.completions.create = AsyncMock(return_value=_make_chat_response(api_payload))

    with patch("src.tools.generate_questions.openai.AsyncOpenAI", return_value=client):
        result = await generate_questions(_witnesses_no_gaps(), _EVIDENCE, _FACTS)

    assert len(result) == 1
    assert result[0]["questions"] == []


# ------------------------------------------------------------------ #
# Empty witnesses list returns empty
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_empty_witnesses_returns_empty():
    result = await generate_questions([], _EVIDENCE, _FACTS)
    assert result == []
