"""Judicial question generation tool for VerdictCouncil.

Generates targeted questions a Judge could ask during a hearing,
based on witness profiles, evidence, and extracted facts.
"""

import json
import logging

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
    witnesses_text: str,
    evidence_text: str,
    facts_text: str,
) -> dict:
    """Call OpenAI to generate judicial questions."""
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
                    "Based on the following witness profiles, evidence, and facts, "
                    "generate targeted judicial questions for each witness.\n\n"
                    f"WITNESSES:\n{witnesses_text}\n\n"
                    f"EVIDENCE:\n{evidence_text}\n\n"
                    f"FACTS:\n{facts_text}\n\n"
                    "For each witness, generate questions that probe credibility "
                    "gaps and inconsistencies.\n\n"
                    "Return JSON with key:\n"
                    "- witnesses: [{witness_name: str, questions: ["
                    "{question: str, rationale: str, targets_weakness: str}]}]"
                ),
            },
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


async def generate_questions(
    witnesses: list[dict],
    evidence: dict,
    facts: dict,
) -> list[dict]:
    """Generate judicial questions for witness cross-examination.

    Analyzes witness profiles with credibility scores against evidence
    and facts to produce probing questions targeting weaknesses.

    Args:
        witnesses: List of witness profile dicts. Each should contain:
            - name (str): Witness name.
            - credibility_score (int): Score from witness analysis (0-100).
            - testimony_summary (str): Summary of their testimony.
            - weaknesses (list[str]): Identified weaknesses or gaps.
        evidence: Evidence analysis output dict with evidence items and
            strength assessments.
        facts: Extracted facts dict from fact reconstruction.

    Returns:
        List of dicts, each containing:
            - witness_name (str): Name of the witness.
            - questions (list[dict]): List of generated questions, each with:
                - question (str): The question text.
                - rationale (str): Why this question matters.
                - targets_weakness (str): Which weakness this probes.

    Raises:
        QuestionGenerationError: If generation fails.
    """
    if not witnesses:
        return []

    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    witnesses_text = json.dumps(witnesses, indent=2, default=str)
    evidence_text = json.dumps(evidence, indent=2, default=str)[:6000]
    facts_text = json.dumps(facts, indent=2, default=str)[:6000]

    try:
        result = await _generate_via_openai(client, witnesses_text, evidence_text, facts_text)
    except json.JSONDecodeError as exc:
        raise QuestionGenerationError(
            f"Failed to parse question generation response: {exc}"
        ) from exc

    # Normalize the output: extract the witness-questions list
    witness_questions = result.get("witnesses", [])

    # Ensure each entry has the expected shape
    output: list[dict] = []
    for entry in witness_questions:
        output.append(
            {
                "witness_name": entry.get("witness_name", "Unknown"),
                "questions": [
                    {
                        "question": q.get("question", ""),
                        "rationale": q.get("rationale", ""),
                        "targets_weakness": q.get("targets_weakness", ""),
                    }
                    for q in entry.get("questions", [])
                ],
            }
        )

    total_questions = sum(len(w["questions"]) for w in output)
    logger.info(
        "Generated %d questions for %d witnesses",
        total_questions,
        len(output),
    )

    return output
