"""Judicial question generation tool for VerdictCouncil.

Generates targeted questions a Judge could ask during a hearing,
based on witness profiles, evidence, and extracted facts.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

import openai

from src.shared.config import settings
from src.shared.retry import retry_with_backoff

logger = logging.getLogger(__name__)


class QuestionGenerationError(Exception):
    """Raised when question generation fails."""


@retry_with_backoff(
    max_retries=2,
    base_delay=1.0,
    retryable_exceptions=(openai.APIConnectionError, openai.RateLimitError),
)
async def _generate_via_openai(
    client: openai.AsyncOpenAI,
    argument_summary: str,
    weaknesses: list[str],
    question_types: list[str],
    max_questions: int,
) -> dict:
    """Call OpenAI to generate judicial questions."""
    types_str = ", ".join(question_types) if question_types else "clarification, challenge"
    weaknesses_text = json.dumps(weaknesses, indent=2)

    response = await client.chat.completions.create(
        model=settings.openai_model_strong_reasoning,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a judicial hearing preparation assistant for Singapore "
                    "lower courts. Generate focused, professionally worded questions "
                    "that a Judge or Tribunal Magistrate could ask during a hearing. "
                    "Questions must be neutral, not leading. They should probe "
                    "weaknesses and credibility gaps without revealing the court's "
                    "preliminary analysis."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Based on the following argument summary and identified weaknesses, "
                    f"generate up to {max_questions} targeted judicial questions.\n\n"
                    f"ARGUMENT SUMMARY:\n{argument_summary}\n\n"
                    f"WEAKNESSES:\n{weaknesses_text}\n\n"
                    f"Question types to generate: {types_str}\n\n"
                    "Return JSON with key:\n"
                    "- questions: [{question: str, rationale: str, "
                    "targets_weakness: str, question_type: str}]"
                ),
            },
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


async def generate_questions(
    argument_summary: Annotated[str, "Summary of the argument or testimony"],
    weaknesses: Annotated[list[str], "List of identified weaknesses or gaps to probe"],
    question_types: Annotated[
        list[str] | None,
        "Types of questions: 'clarification' | 'challenge' | 'exploration' | 'credibility'",
    ] = None,
    max_questions: Annotated[int, "Maximum number of questions to generate"] = 5,
) -> list[dict]:
    """Generate suggested judicial questions based on argument analysis.

    Analyzes an argument summary and identified weaknesses to produce
    probing questions for judicial hearings.

    Args:
        argument_summary: Summary of the argument or testimony to analyze.
        weaknesses: List of identified weaknesses or gaps to probe.
        question_types: Types of questions to generate. Defaults to
            ['clarification', 'challenge'].
        max_questions: Maximum number of questions to generate. Defaults to 5.

    Returns:
        List of dicts, each containing:
            - question (str): The question text.
            - rationale (str): Why this question matters.
            - targets_weakness (str): Which weakness this probes.
            - question_type (str): Type of question generated.

    Raises:
        QuestionGenerationError: If generation fails.
    """
    if question_types is None:
        question_types = ["clarification", "challenge"]

    if not argument_summary:
        return []

    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    try:
        result = await _generate_via_openai(
            client, argument_summary, weaknesses, question_types, max_questions
        )
    except json.JSONDecodeError as exc:
        raise QuestionGenerationError(
            f"Failed to parse question generation response: {exc}"
        ) from exc

    # Normalize the output: extract the questions list
    raw_questions = result.get("questions", [])

    # Ensure each entry has the expected shape
    output: list[dict] = []
    for q in raw_questions[:max_questions]:
        output.append(
            {
                "question": q.get("question", ""),
                "rationale": q.get("rationale", ""),
                "targets_weakness": q.get("targets_weakness", ""),
                "question_type": q.get("question_type", ""),
            }
        )

    logger.info("Generated %d questions from argument analysis", len(output))

    return output
