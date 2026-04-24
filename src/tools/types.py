"""TypedDict definitions for VerdictCouncil tool parameters.

These types mirror the TOOL_SCHEMAS in src.pipeline.runner and are used
to annotate tool function signatures so SAM can auto-generate rich
parameter schemas from type hints.
"""

from __future__ import annotations

from typing import TypedDict


class CrossReferenceSegment(TypedDict):
    """A single document segment for cross-reference comparison.

    Attributes:
        doc_id: Identifier of the source document.
        text: Text content of the segment.
        page: Page number where the segment appears.
        paragraph: Paragraph number within the page.
    """

    doc_id: str
    text: str
    page: int
    paragraph: int


class _TimelineFactRequired(TypedDict):
    """Required fields for a timeline fact entry."""

    date: str
    event: str


class TimelineFact(_TimelineFactRequired, total=False):
    """A single fact entry for timeline construction.

    Required:
        date: Date/time string in any recognizable format.
        event: Description of what happened.

    Optional:
        fact_id: Unique identifier for the fact.
        source_refs: References to source documents.
    """

    fact_id: str
    source_refs: list[str]
