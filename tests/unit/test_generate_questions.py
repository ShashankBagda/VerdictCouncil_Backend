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


_ARGUMENT_SUMMARY = (
    "Alice Tan claims she witnessed the accident from across the street. "
    "Her testimony places the collision at 3:15pm but CCTV shows 3:45pm."
)
_WEAKNESSES = ["Poor viewing angle", "Delayed report", "Time discrepancy with CCTV"]


# ------------------------------------------------------------------ #
# Weaknesses produce targeted questions
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_weaknesses_produce_questions(_openai_client):
    client = _openai_client
    api_payload = {
        "questions": [
            {
                "question": "Can you describe your exact position?",
                "rationale": "Viewing angle is questionable.",
                "targets_weakness": "Poor viewing angle",
                "question_type": "challenge",
            },
            {
                "question": "Why did you wait 3 days to report?",
                "rationale": "Delay undermines reliability.",
                "targets_weakness": "Delayed report",
                "question_type": "credibility",
            },
        ]
    }
    client.chat.completions.create = AsyncMock(return_value=_make_chat_response(api_payload))

    with patch("src.tools.generate_questions.openai.AsyncOpenAI", return_value=client):
        result = await generate_questions(_ARGUMENT_SUMMARY, _WEAKNESSES)

    assert len(result) == 2
    assert result[0]["targets_weakness"] == "Poor viewing angle"
    assert result[1]["question_type"] == "credibility"


# ------------------------------------------------------------------ #
# No weaknesses -> empty questions
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_no_weaknesses_produces_empty_questions(_openai_client):
    client = _openai_client
    api_payload = {"questions": []}
    client.chat.completions.create = AsyncMock(return_value=_make_chat_response(api_payload))

    with patch("src.tools.generate_questions.openai.AsyncOpenAI", return_value=client):
        result = await generate_questions(_ARGUMENT_SUMMARY, [])

    assert result == []


# ------------------------------------------------------------------ #
# Empty argument summary returns empty
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_empty_argument_summary_returns_empty():
    result = await generate_questions("", _WEAKNESSES)
    assert result == []
