"""Cross-reference tool for comparing documents in VerdictCouncil.

Analyzes pairs of parsed documents to find contradictions and
corroborations across evidence items.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

import openai

from src.shared.config import settings
from src.shared.retry import retry_with_backoff
from src.tools.types import CrossReferenceSegment

logger = logging.getLogger(__name__)


class CrossReferenceError(Exception):
    """Raised when cross-reference analysis fails."""


@retry_with_backoff(
    max_retries=2,
    base_delay=1.0,
    retryable_exceptions=(openai.APIConnectionError, openai.RateLimitError),
)
async def _analyze_documents(
    client: openai.AsyncOpenAI,
    documents_text: str,
    case_domain: str,
) -> dict:
    """Call OpenAI to compare documents for contradictions and corroborations."""
    response = await client.chat.completions.create(
        model=settings.openai_model_strong_reasoning,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a legal evidence analyst specialising in Singapore "
                    f"{case_domain} cases. Compare the provided documents and "
                    "identify relationships between them. Be precise and cite "
                    "specific text from each document."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Compare these documents:\n\n{documents_text}\n\n"
                    "Return JSON with keys:\n"
                    "- contradictions: [{doc_a: str (doc identifier), "
                    "doc_b: str, description: str, "
                    "severity: 'critical'|'moderate'|'minor'}]\n"
                    "- corroborations: [{doc_ids: [str], description: str, "
                    "strength: 'strong'|'moderate'|'weak'}]"
                ),
            },
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


async def cross_reference(
    segments: Annotated[
        list[CrossReferenceSegment],
        "List of document segments to compare. Each segment: {doc_id, text, page, paragraph}",
    ],
    check_type: Annotated[
        str,
        "Type of cross-reference check: 'contradiction' | 'corroboration' | 'all'",
    ],
) -> dict:
    """Compare document segments to find contradictions and corroborations.

    Args:
        segments: List of document segments to compare. Each should have
            at minimum a doc identifier and text content.
        check_type: Type of cross-reference check to perform:
            'contradiction', 'corroboration', or 'all'.

    Returns:
        Dictionary with keys:
            - contradictions: list of {doc_a, doc_b, description, severity}
            - corroborations: list of {doc_ids, description, strength}

    Raises:
        CrossReferenceError: If analysis fails.
    """
    if len(segments) < 2:
        return {
            "contradictions": [],
            "corroborations": [],
        }

    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    # Serialize segments for the prompt, keeping only relevant fields
    doc_summaries = []
    for seg in segments:
        doc_summaries.append(
            {
                "doc_id": seg.get("file_id") or seg.get("doc_id", "unknown"),
                "filename": seg.get("filename", ""),
                "text": seg.get("text", "")[:8000],  # Truncate to fit context
            }
        )

    documents_text = json.dumps(doc_summaries, indent=2)

    try:
        result = await _analyze_documents(client, documents_text, check_type)
    except json.JSONDecodeError as exc:
        raise CrossReferenceError(f"Failed to parse cross-reference analysis response: {exc}") from exc

    # Ensure expected keys exist with proper defaults
    contradictions = result.get("contradictions", [])
    corroborations = result.get("corroborations", [])

    logger.info(
        "Cross-reference complete: %d contradictions, %d corroborations across %d segments",
        len(contradictions),
        len(corroborations),
        len(segments),
    )

    return {
        "contradictions": contradictions,
        "corroborations": corroborations,
    }
