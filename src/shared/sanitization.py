import re
import unicodedata

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_homoglyphs(text: str) -> str:
    """Normalize unicode to ASCII-equivalent to defeat homoglyph bypasses.

    Zero-width characters are stripped. Confusable lookalikes (Cyrillic а, etc.)
    are collapsed via NFKD + ASCII encoding.
    """
    # Strip zero-width characters (U+200B, U+200C, U+200D, U+FEFF, etc.)
    cleaned = re.sub(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad]", "", text)
    # NFKD normalize then drop non-ASCII (collapses homoglyphs)
    normalized = unicodedata.normalize("NFKD", cleaned)
    return normalized


# ---------------------------------------------------------------------------
# Delimiter patterns — fixed-string matching where possible
# ---------------------------------------------------------------------------

# Fixed strings that should never appear in legal documents
_DELIMITER_STRINGS = [
    "<|im_start|>",
    "<|im_end|>",
    "<|endoftext|>",
    "<|system|>",
    "[INST]",
    "[/INST]",
    "<<SYS>>",
    "<</SYS>>",
]

# Patterns that match delimiter-wrapped content (must run BEFORE the fixed-string
# delimiter substitution below — otherwise the closing delimiter is replaced
# first and the pair-matching regex no longer fires, leaving the injected body
# in place).
_WRAPPED_CONTENT_PATTERNS = [
    # OpenAI ChatML: <|im_start|>role\ncontent<|im_end|>
    re.compile(r"<\|im_start\|>.*?<\|im_end\|>", re.DOTALL | re.IGNORECASE),
    # Llama instruction blocks: [INST]...[/INST]
    re.compile(r"\[INST\].*?\[/INST\]", re.DOTALL | re.IGNORECASE),
    # Llama system blocks: <<SYS>>...<</SYS>>
    re.compile(r"<<SYS>>.*?<</SYS>>", re.DOTALL | re.IGNORECASE),
]

# Regex patterns for delimiters that need flexible matching
_INJECTION_PATTERNS = [
    re.compile(r"<\|[a-z_]+\|>", re.IGNORECASE),  # OpenAI-style delimiters
    re.compile(r"```system\b.*?```", re.DOTALL),  # Markdown system blocks
]

# XML delimiter patterns used in structured prompts
_XML_INJECTION_PATTERNS = [
    re.compile(r"</?(system|instruction|tool_call|function_call)\b[^>]*>", re.IGNORECASE),
]

# Natural language prompt injection patterns
# Use word boundaries and more specific patterns to reduce false positives
_NL_INJECTION_PATTERNS = [
    re.compile(r"IGNORE\s+.*?PREVIOUS\s+.*?INSTRUCTIONS", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(?:a\s+|an\s+|my\s+|the\s+)", re.IGNORECASE),
    re.compile(r"forget\s+(?:all\s+)?your\s+(?:previous\s+)?instructions", re.IGNORECASE),
    re.compile(
        r"disregard\s+.*?(?:above|previous|prior)\s+(?:instructions|rules|guidelines)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:new|override|replace)\s+system\s+(?:prompt|instructions?|message)", re.IGNORECASE
    ),
    re.compile(r"act\s+as\s+(?:if|though)\s+you\s+(?:are|were)\b", re.IGNORECASE),
    re.compile(r"do\s+not\s+follow\s+.*?(?:instructions|rules|guidelines)", re.IGNORECASE),
]


def _check_delimiter_strings(text: str) -> bool:
    """Check for fixed delimiter strings (case-insensitive, homoglyph-resistant)."""
    normalized = _strip_homoglyphs(text).lower()
    return any(delim.lower() in normalized for delim in _DELIMITER_STRINGS)


def sanitize_document_content(text: str) -> str:
    """Remove prompt injection patterns from document content.

    Applied to all text extracted by parse_document before it enters
    the agent pipeline.
    """
    result = text
    # Strip delimiter-wrapped blocks first so any prompt body between
    # paired delimiters (e.g. `<|im_start|>system\nYou are evil<|im_end|>`)
    # is removed in full before the fixed-string pass nukes the closing
    # delimiter and orphans the body.
    for pattern in _WRAPPED_CONTENT_PATTERNS:
        result = pattern.sub("[CONTENT_REMOVED]", result)
    # Fixed-string delimiters: catch any unpaired delimiters left behind
    for delim in _DELIMITER_STRINGS:
        result = result.replace(delim, "[CONTENT_REMOVED]")
    # Regex-based patterns
    for pattern in _INJECTION_PATTERNS:
        result = pattern.sub("[CONTENT_REMOVED]", result)
    for pattern in _XML_INJECTION_PATTERNS:
        result = pattern.sub("[TAG_REMOVED]", result)
    for pattern in _NL_INJECTION_PATTERNS:
        result = pattern.sub("[INJECTION_REMOVED]", result)
    return result


def detect_injection(text: str) -> bool:
    """Return True if text contains any known injection pattern.

    Normalizes unicode first to catch homoglyph/zero-width bypasses.
    """
    normalized = _strip_homoglyphs(text)
    # Fixed-string delimiter check
    if _check_delimiter_strings(normalized):
        return True
    # Regex patterns
    for pattern in _INJECTION_PATTERNS + _XML_INJECTION_PATTERNS + _NL_INJECTION_PATTERNS:
        if pattern.search(normalized):
            return True
    return False


def sanitize_user_input(text: str) -> str:
    """Sanitize user-provided text (case descriptions, notes)."""
    result = sanitize_document_content(text)
    # Also strip null bytes
    result = result.replace("\x00", "")
    return result
