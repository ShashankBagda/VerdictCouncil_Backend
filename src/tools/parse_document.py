"""Document parsing tool wrapping the OpenAI Files API for VerdictCouncil.

Extracts text content, tables, and metadata from legal filings.
Supports PDF, DOCX, images (with OCR), and plain text files.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

import openai

from src.shared.config import settings
from src.shared.retry import retry_with_backoff
from src.shared.sanitization import sanitize_document_content

logger = logging.getLogger(__name__)


class DocumentParseError(Exception):
    """Raised when document parsing fails."""


@retry_with_backoff(
    max_retries=2,
    base_delay=1.0,
    retryable_exceptions=(openai.APIConnectionError, openai.RateLimitError),
)
async def _extract_via_openai(
    client: openai.AsyncOpenAI,
    file_id: str,
    extract_tables: bool,
    ocr_enabled: bool,
) -> dict:
    """Call OpenAI chat completion to extract text from a file attachment."""
    table_instruction = "Also extract all tables as structured data." if extract_tables else ""
    ocr_instruction = "This may be a scanned document - use OCR." if ocr_enabled else ""

    response = await client.chat.completions.create(
        model=settings.openai_model_lightweight,
        messages=[
            {
                "role": "system",
                "content": (
                    "Extract all text content from the provided document. "
                    "Preserve paragraph structure and formatting. "
                    "If the document contains tables, extract each table as a "
                    "JSON array of rows."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "file",
                        "file": {"file_id": file_id},
                    },
                    {
                        "type": "text",
                        "text": (
                            "Extract all text from this document. "
                            f"{table_instruction} "
                            f"{ocr_instruction} "
                            "Return JSON with keys: "
                            "text (full document text), "
                            "pages (list of objects with page_number, text, and tables), "
                            "tables (flat list of all tables, each with page_number and rows), "
                            "page_count, word_count."
                        ),
                    },
                ],
            },
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


async def parse_document(
    file_id: Annotated[str, "OpenAI File ID of the uploaded document"],
    extract_tables: Annotated[bool, "Whether to extract tabular data"] = True,
    ocr_enabled: Annotated[bool, "Whether to enable OCR for scanned documents"] = False,
) -> dict:
    """Parse an uploaded document via the OpenAI Files API.

    Args:
        file_id: OpenAI File ID of the uploaded document (e.g., "file-abc123").
        extract_tables: Whether to extract tabular data. Defaults to True.
        ocr_enabled: Whether to enable OCR for scanned/image documents.
            Defaults to False.

    Returns:
        Dictionary with keys: file_id, filename, content_type, text, pages,
        tables, metadata, parsing_notes.

    Raises:
        DocumentParseError: If parsing fails or no text is extracted.
    """
    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    # Retrieve file metadata
    try:
        file_info = await client.files.retrieve(file_id)
    except openai.APIError as exc:
        raise DocumentParseError(f"Failed to retrieve file metadata for {file_id}: {exc}") from exc

    filename = file_info.filename
    content_type = getattr(file_info, "content_type", "application/octet-stream")

    parsing_notes: list[str] = []
    metadata = {
        "filename": filename,
        "content_type": content_type,
    }

    # Extract content via OpenAI chat completion with file attachment
    try:
        parsed = await _extract_via_openai(client, file_id, extract_tables, ocr_enabled)
    except (json.JSONDecodeError, openai.APIError) as exc:
        raise DocumentParseError(f"Failed to parse document {file_id}: {exc}") from exc

    extracted_text = parsed.get("text", "")
    extracted_tables = parsed.get("tables", []) if extract_tables else []
    raw_pages = parsed.get("pages", [])
    metadata["page_count"] = parsed.get("page_count")
    metadata["word_count"] = parsed.get("word_count")

    if not extracted_text.strip():
        raise DocumentParseError(
            f"No text content extracted from document {file_id}. "
            "Document may be corrupt or unsupported format."
        )

    # Sanitize extracted text to strip prompt-injection patterns
    extracted_text = sanitize_document_content(extracted_text)

    # Sanitize per-page text
    pages: list[dict] = []
    for page in raw_pages:
        page_text = sanitize_document_content(page.get("text", ""))
        pages.append(
            {
                "page_number": page.get("page_number"),
                "text": page_text,
                "tables": page.get("tables", []),
            }
        )

    if not pages:
        # If the model didn't return per-page breakdown, synthesize a single page
        pages = [{"page_number": 1, "text": extracted_text, "tables": extracted_tables}]
        parsing_notes.append("Per-page breakdown unavailable; full text placed on page 1.")

    logger.info(
        "Parsed document %s (%s): %d pages, %d tables",
        file_id,
        filename,
        len(pages),
        len(extracted_tables),
    )

    return {
        "file_id": file_id,
        "filename": filename,
        "content_type": content_type,
        "text": extracted_text,
        "pages": pages,
        "tables": extracted_tables,
        "metadata": metadata,
        "parsing_notes": parsing_notes,
    }
