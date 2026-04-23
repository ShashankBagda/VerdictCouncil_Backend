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
from src.shared.sanitization import SanitizationResult, classify_text_async, sanitize_text

logger = logging.getLogger(__name__)

_client: openai.AsyncOpenAI | None = None


def _get_client() -> openai.AsyncOpenAI:
    global _client
    if _client is None:
        _client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


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
    """Call OpenAI Responses API to extract text from a file attachment."""
    table_instruction = "Also extract all tables as structured data." if extract_tables else ""
    ocr_instruction = "This may be a scanned document - use OCR." if ocr_enabled else ""

    response = await client.responses.create(
        model=settings.openai_model_lightweight,
        input=[
            {
                "type": "input_file",
                "file_id": file_id,
            },
            {
                "type": "input_text",
                "text": (
                    "Extract all text content from this document. "
                    "Preserve paragraph structure and formatting. "
                    "If the document contains tables, extract each table as a "
                    "JSON array of rows. "
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
        text={"format": {"type": "json_object"}},
    )
    return json.loads(response.output_text)


async def parse_document(
    file_id: Annotated[str, "OpenAI File ID of the uploaded document"],
    extract_tables: Annotated[bool, "Whether to extract tabular data"] = True,
    ocr_enabled: Annotated[bool, "Whether to enable OCR for scanned/image documents"] = False,
    run_classifier: Annotated[bool, "Enable llm-guard DeBERTa-v3 classifier on top of regex"] = False,
) -> dict:
    """Parse an uploaded document via the OpenAI Files API.

    D13 NOTE: The in-process pipeline runner short-circuits this call when
    pages for `file_id` are already hydrated in CaseState.raw_documents
    (see runner.py:_execute_tool_call / _get_cached_pages).  This function
    is only reached on a true cache miss, or when called directly by the
    domain-upload route (which never has state).  No `state` parameter is
    needed here because there is no SAM DynamicTool wrapping this function
    — the tool is `function_name: parse_document` (in-process only), so
    the mesh path never calls it.

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
    client = _get_client()

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

    raw_extracted_text = parsed.get("text", "")
    extracted_tables = parsed.get("tables", []) if extract_tables else []
    raw_pages = parsed.get("pages", [])
    metadata["page_count"] = parsed.get("page_count")
    metadata["word_count"] = parsed.get("word_count")

    if not raw_extracted_text.strip():
        raise DocumentParseError(
            f"No text content extracted from document {file_id}. "
            "Document may be corrupt or unsupported format."
        )

    # Sanitize once per page (single pass — avoids double-scanning the same content).
    # Full document text is derived by joining sanitized pages so the two are consistent.
    total_regex_hits = 0
    total_classifier_hits = 0
    pages: list[dict] = []

    if raw_pages:
        for page in raw_pages:
            regex_result = sanitize_text(page.get("text", ""))
            page_text = regex_result.text
            total_regex_hits += regex_result.regex_hits

            if run_classifier and settings.classifier_sanitizer_enabled:
                page_text, _score = await classify_text_async(page_text)
                if page_text == "[CONTENT_BLOCKED_BY_SCANNER]":
                    total_classifier_hits += 1

            pages.append(
                {
                    "page_number": page.get("page_number"),
                    "text": page_text,
                    "tables": page.get("tables", []),
                }
            )
        extracted_text = "\n".join(p["text"] for p in pages)
    else:
        # No per-page breakdown: sanitize the full text once and synthesise a single page
        regex_result = sanitize_text(raw_extracted_text)
        extracted_text = regex_result.text
        total_regex_hits += regex_result.regex_hits

        if run_classifier and settings.classifier_sanitizer_enabled:
            extracted_text, _score = await classify_text_async(extracted_text)
            if extracted_text == "[CONTENT_BLOCKED_BY_SCANNER]":
                total_classifier_hits += 1

        pages = [{"page_number": 1, "text": extracted_text, "tables": extracted_tables}]
        parsing_notes.append("Per-page breakdown unavailable; full text placed on page 1.")

    sanitization = SanitizationResult(
        text=extracted_text,
        regex_hits=total_regex_hits,
        classifier_hits=total_classifier_hits,
        chunks_scanned=len(pages),
    )

    logger.info(
        "Parsed document %s (%s): %d pages, %d tables, regex_hits=%d, classifier_hits=%d",
        file_id,
        filename,
        len(pages),
        len(extracted_tables),
        total_regex_hits,
        total_classifier_hits,
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
        "sanitization": sanitization,
    }
