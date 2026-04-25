"""Prompt registry — LangSmith pull with local fallback (Sprint 1 1.C3a.3).

Replaces the legacy `AGENT_PROMPTS` literal dict that lived in
`src/pipeline/graph/prompts.py`. The agent factory calls
`get_prompt(phase, corrections=None)` to obtain the system prompt for
the active phase. Lookup order:

1. **LangSmith pull** (cached via `lru_cache(maxsize=64)`). The prompt
   ID is the bare `vc-<phase>` identifier the migration script pushed
   to the personal namespace. The commit hash auto-flows into LangSmith
   trace metadata so we don't thread it through Python.
2. **Local file fallback** — `prompts/<phase>.md`. Used when LangSmith
   is unreachable (CI without `LANGSMITH_API_KEY`, offline dev,
   transient API errors). Logged as a warning so operational drift is
   visible.

Judge corrections (`corrections=...`) push a new commit to LangSmith
with the correction appended, bust the LangSmith cache for that prompt,
and return the corrected text. This replaces the runtime concat the
legacy pipeline did inside `nodes/common.py:121-135` (deleted in
1.A1.6).

The factory imports `get_prompt` directly; no other module should.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"

# Maps factory phase name → LangSmith prompt identifier (bare names
# pushed to the personal namespace by `scripts/migrate_prompts_to_langsmith.py`).
PHASE_TO_PROMPT_ID: dict[str, str] = {
    "intake": "vc-intake",
    "research-evidence": "vc-research-evidence",
    "research-facts": "vc-research-facts",
    "research-witnesses": "vc-research-witnesses",
    "research-law": "vc-research-law",
    "synthesis": "vc-synthesis",
    "audit": "vc-audit",
}

# Maps factory phase name → local fallback markdown filename under `prompts/`.
PHASE_TO_FILE: dict[str, str] = {
    "intake": "intake.md",
    "research-evidence": "research-evidence.md",
    "research-facts": "research-facts.md",
    "research-witnesses": "research-witnesses.md",
    "research-law": "research-law.md",
    "synthesis": "synthesis.md",
    "audit": "audit.md",
}


def _make_client():  # noqa: ANN202 — return type is a langsmith.Client
    """Lazy LangSmith client — keeps import-time clean when no key is set."""
    from langsmith import Client

    return Client()


@lru_cache(maxsize=64)
def _resolve_from_langsmith(prompt_id: str) -> str | None:
    """Pull the latest commit's system content from LangSmith. None on failure.

    Wrapped in `lru_cache` so a hot path (multiple subagents resolving
    the same prompt within a run) costs one network round-trip per
    process. `cache_clear()` is invoked from `get_prompt` after a
    judge-correction push so subsequent calls see the new commit.
    """
    if not os.environ.get("LANGSMITH_API_KEY"):
        return None
    try:
        client = _make_client()
        template: ChatPromptTemplate = client.pull_prompt(prompt_id)
    except Exception as exc:  # noqa: BLE001 — SDK throws a few different shapes
        logger.warning("LangSmith pull_prompt(%r) failed: %s", prompt_id, exc)
        return None

    if hasattr(template, "messages") and template.messages:
        first = template.messages[0]
        if hasattr(first, "prompt") and hasattr(first.prompt, "template"):
            return first.prompt.template
    logger.warning("LangSmith pull_prompt(%r) returned unexpected shape", prompt_id)
    return None


def _resolve_from_local(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Local fallback prompt missing: {path}. Run "
            "`scripts/migrate_prompts_to_langsmith.py` to repopulate, or "
            "add the file before re-running."
        )
    return path.read_text()


def _push_correction(prompt_id: str, corrected_text: str) -> None:
    """Push a new LangSmith commit carrying the corrected prompt."""
    client = _make_client()
    template = ChatPromptTemplate.from_messages([("system", corrected_text)])
    client.push_prompt(
        prompt_id,
        object=template,
        commit_description="Judge correction (1.C3a.3)",
        commit_tags=["judge-correction"],
    )


def get_prompt(phase: str, corrections: str | None = None) -> str:
    """Return the system prompt for `phase`, optionally with judge corrections.

    Args:
        phase: One of the keys in `PHASE_TO_PROMPT_ID`
            (`intake`, `research-{evidence,facts,witnesses,law}`,
            `synthesis`, `audit`).
        corrections: Optional judge-supplied corrective text. When
            provided, append to the base prompt, push the result as a
            new LangSmith commit, bust the cache, and return the
            corrected text.

    Raises:
        KeyError: when `phase` is unknown.
        FileNotFoundError: when LangSmith is unreachable AND the local
            fallback file does not exist.
    """
    if phase not in PHASE_TO_PROMPT_ID:
        raise KeyError(f"Unknown phase: {phase!r}; expected one of {sorted(PHASE_TO_PROMPT_ID)}")

    prompt_id = PHASE_TO_PROMPT_ID[phase]
    filename = PHASE_TO_FILE[phase]

    base = _resolve_from_langsmith(prompt_id)
    if base is None:
        logger.info("Falling back to local prompt for %r (LangSmith unavailable)", phase)
        base = _resolve_from_local(filename)

    if not corrections:
        return base

    corrected = f"{base}\n\nAdditional instructions from judge:\n{corrections}"
    _push_correction(prompt_id, corrected)
    _resolve_from_langsmith.cache_clear()
    return corrected
