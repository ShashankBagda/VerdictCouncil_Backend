import re

# Patterns that could be used for prompt injection
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

# Natural language prompt injection patterns
_NL_INJECTION_PATTERNS = [
    re.compile(r"IGNORE\s+.*?PREVIOUS\s+.*?INSTRUCTIONS", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+", re.IGNORECASE),
    re.compile(r"forget\s+your\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+.*?above", re.IGNORECASE),
]


def sanitize_document_content(text: str) -> str:
    """Remove prompt injection patterns from document content.

    Applied to all text extracted by parse_document before it enters
    the agent pipeline.
    """
    result = text
    for pattern in _INJECTION_PATTERNS:
        result = pattern.sub("[CONTENT_REMOVED]", result)
    for pattern in _XML_INJECTION_PATTERNS:
        result = pattern.sub("[TAG_REMOVED]", result)
    for pattern in _NL_INJECTION_PATTERNS:
        result = pattern.sub("[INJECTION_REMOVED]", result)
    return result


def detect_injection(text: str) -> bool:
    """Return True if text contains any known injection pattern."""
    for pattern in _INJECTION_PATTERNS + _XML_INJECTION_PATTERNS + _NL_INJECTION_PATTERNS:
        if pattern.search(text):
            return True
    return False


def sanitize_user_input(text: str) -> str:
    """Sanitize user-provided text (case descriptions, notes)."""
    result = sanitize_document_content(text)
    # Also strip null bytes
    result = result.replace("\x00", "")
    return result
