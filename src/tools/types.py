"""TypedDict definitions for VerdictCouncil tool parameters.

These types mirror the TOOL_SCHEMAS in src.pipeline.runner and are used
to annotate tool function signatures so SAM can auto-generate rich
parameter schemas from type hints.
"""

from __future__ import annotations

from typing import TypedDict


class ParseDocumentInput(TypedDict, total=False):
    """Parameters for the parse_document tool.

    Attributes:
        file_id: OpenAI File ID of the uploaded document.
        extract_tables: Whether to extract tabular data. Defaults to True.
        ocr_enabled: Whether to enable OCR for scanned documents. Defaults to False.
    """

    file_id: str
    extract_tables: bool
    ocr_enabled: bool


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


class TimelineFact(TypedDict, total=False):
    """A single fact entry for timeline construction.

    Attributes:
        fact_id: Unique identifier for the fact.
        date: Date/time string in any recognizable format.
        event: Description of what happened.
        source_refs: References to source documents.
    """

    fact_id: str
    date: str
    event: str
    source_refs: list[str]


class GenerateQuestionsInput(TypedDict, total=False):
    """Parameters for the generate_questions tool.

    Attributes:
        argument_summary: Summary of the argument or testimony.
        weaknesses: List of identified weaknesses or gaps to probe.
        question_types: Types of questions to generate.
        max_questions: Maximum number of questions to generate.
    """

    argument_summary: str
    weaknesses: list[str]
    question_types: list[str]
    max_questions: int
