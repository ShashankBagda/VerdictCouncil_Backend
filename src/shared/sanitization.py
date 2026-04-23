from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layer-1: regex fast-path (delimiter-based injection patterns)
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS = [
    re.compile(r"<\|im_start\|>.*?<\|im_end\|>", re.DOTALL),  # OpenAI chat delimiters
    re.compile(r"<\|.*?\|>", re.DOTALL),  # Other OpenAI-style delimiters
    re.compile(r"\[INST\].*?\[/INST\]", re.DOTALL),  # Llama-style delimiters
    re.compile(r"<<SYS>>.*?<</SYS>>", re.DOTALL),  # System prompt injection
    re.compile(r"```system\b.*?```", re.DOTALL),  # Markdown system blocks
]

# XML delimiter patterns used in structured prompts
_XML_INJECTION_PATTERNS = [
    re.compile(r"</?(system|instruction|tool_call|function_call)\b[^>]*>", re.IGNORECASE),
]


@dataclass(frozen=True)
class SanitizationResult:
    text: str
    regex_hits: int
    classifier_hits: int  # 0 when classifier is disabled
    chunks_scanned: int   # pages processed


def detect_injection(text: str) -> bool:
    """Return True when text matches any known prompt-injection pattern.

    Used by guardrails.check_input_injection as the layer-1 regex scan
    before falling back to a lightweight LLM classifier.
    """
    if not text:
        return False
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS) or any(
        pattern.search(text) for pattern in _XML_INJECTION_PATTERNS
    )


def _apply_regex(text: str) -> tuple[str, int]:
    """Apply regex patterns to text. Returns (sanitized_text, hit_count)."""
    result = text
    hits = 0
    for pattern in _INJECTION_PATTERNS:
        new = pattern.sub("[CONTENT_REMOVED]", result)
        if new != result:
            hits += 1
        result = new
    for pattern in _XML_INJECTION_PATTERNS:
        new = pattern.sub("[TAG_REMOVED]", result)
        if new != result:
            hits += 1
        result = new
    return result, hits


def sanitize_text(text: str) -> SanitizationResult:
    """Run layer-1 regex sanitization. Returns a SanitizationResult.

    This is the primary sanitization function. Call sanitize_document_content()
    for the simple string-in / string-out interface used by legacy callers.
    """
    sanitized, regex_hits = _apply_regex(text)
    return SanitizationResult(
        text=sanitized,
        regex_hits=regex_hits,
        classifier_hits=0,
        chunks_scanned=0,
    )


def sanitize_document_content(text: str) -> str:
    """Remove prompt injection patterns from document content.

    Thin wrapper around sanitize_text() for callers that only need the text.
    """
    return sanitize_text(text).text


def sanitize_user_input(text: str) -> str:
    """Sanitize user-provided text (case descriptions, notes)."""
    result = sanitize_document_content(text)
    result = result.replace("\x00", "")
    return result


# ---------------------------------------------------------------------------
# Layer-2: llm-guard DeBERTa-v3 classifier (semantic injection detection)
# ---------------------------------------------------------------------------

_scanner: Any | None = None
_scanner_init_failed: bool = False


def _get_prompt_injection_scanner() -> Any:
    """Return the cached PromptInjection scanner, initialising it on first call.

    Lazy-initialised so model weights (~415 MB) are not loaded at import time —
    Alembic, tests, and the pipeline runner are unaffected until a classified
    scan is actually requested.

    Raises RuntimeError if llm-guard is missing or model init fails.
    """
    global _scanner, _scanner_init_failed
    if _scanner_init_failed:
        raise RuntimeError("PromptInjection scanner failed to initialise; uploads blocked.")
    if _scanner is not None:
        return _scanner
    try:
        from llm_guard.input_scanners.prompt_injection import (  # noqa: I001
            MatchType,
            PromptInjection,
            V2_MODEL,
        )

        _scanner = PromptInjection(model=V2_MODEL, threshold=0.9, match_type=MatchType.CHUNKS)
        logger.info("PromptInjection scanner initialised (model=%s)", V2_MODEL.path)
    except Exception as exc:
        _scanner_init_failed = True
        raise RuntimeError(f"PromptInjection scanner init failed: {exc}") from exc
    return _scanner


def _classify_sync(text: str) -> tuple[str, float]:
    """Synchronous classifier call. Returns (text_or_blocked, risk_score).

    When injection is detected (is_valid=False), the text is replaced with
    [CONTENT_BLOCKED_BY_SCANNER] to prevent the payload reaching the vector
    store. The original text is never stored in that case.
    """
    scanner = _get_prompt_injection_scanner()
    # scan() returns (sanitized_prompt, is_valid, risk_score).
    # The library does not redact; we handle replacement ourselves.
    _sanitized, is_valid, risk_score = scanner.scan(text)
    if not is_valid:
        logger.warning(
            "prompt_injection_blocked",
            extra={"risk_score": risk_score, "text_len": len(text)},
        )
        return "[CONTENT_BLOCKED_BY_SCANNER]", risk_score
    return text, risk_score


async def classify_text_async(text: str) -> tuple[str, float]:
    """Async wrapper around the sync classifier. Offloads to a thread pool
    so the FastAPI event loop is not blocked during DeBERTa inference.
    """
    return await asyncio.to_thread(_classify_sync, text)
