"""Sprint 1 1.C3a.2 — unit tests for the prompt-migration script.

Tests the local-loading + idempotency logic without touching LangSmith.
The actual `push_prompt` / `pull_prompt` calls are mocked. A live push
is verified manually after the user runs the script with their
`LANGSMITH_API_KEY`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain_core.prompts import ChatPromptTemplate

from scripts.migrate_prompts_to_langsmith import (
    PROMPT_FILES,
    PROMPTS_DIR,
    _build_template,
    _load_local,
    _push_one,
)


def test_all_seven_prompt_files_exist() -> None:
    """Every prompt declared in the registry must have a matching .md file."""
    missing = [name for name in PROMPT_FILES if not (PROMPTS_DIR / name).exists()]
    assert not missing, (
        f"Prompt registry references files that don't exist: {missing}. "
        f"Add them to {PROMPTS_DIR.relative_to(Path.cwd().parent)}/."
    )


def test_prompt_count_matches_topology() -> None:
    """6-phase topology: 1 intake + 4 research + synthesis + audit = 7 prompts."""
    assert len(PROMPT_FILES) == 7, (
        f"Expected 7 phase-aligned prompts (intake + 4 research + synthesis "
        f"+ audit); got {len(PROMPT_FILES)}: {sorted(PROMPT_FILES)}"
    )


def test_every_prompt_references_pydantic_schema() -> None:
    """Acceptance criterion: each prompt explicitly references its response schema."""
    schema_marker = "src/pipeline/graph/schemas.py"
    for filename in PROMPT_FILES:
        content = _load_local(filename)
        assert schema_marker in content, (
            f"prompts/{filename} must reference its Pydantic response "
            f"schema (`{schema_marker}::<ClassName>`) so the agent knows the "
            "output contract. Acceptance criterion 1.C3a.2."
        )


def test_build_template_produces_chat_prompt_template() -> None:
    template = _build_template("hello world")
    assert isinstance(template, ChatPromptTemplate)
    assert template.messages, "ChatPromptTemplate must contain at least one message"


def test_push_one_unchanged_when_remote_matches_local() -> None:
    """Idempotency: a second run after a successful push is a no-op."""
    client = MagicMock()
    local = _load_local("intake.md")

    # Mock the remote pull to return a template that wraps the same content.
    fake_template = ChatPromptTemplate.from_messages([("system", local)])
    client.pull_prompt.return_value = fake_template

    action = _push_one(
        client,
        "verdict-council/intake",
        "intake.md",
        dry_run=False,
    )
    assert action == "unchanged"
    client.push_prompt.assert_not_called()


def test_push_one_updated_when_remote_drifts() -> None:
    """A drifted remote triggers a fresh push."""
    client = MagicMock()
    fake_template = ChatPromptTemplate.from_messages([("system", "old content")])
    client.pull_prompt.return_value = fake_template

    action = _push_one(
        client,
        "verdict-council/intake",
        "intake.md",
        dry_run=False,
    )
    assert action == "updated"
    client.push_prompt.assert_called_once()


def test_push_one_created_when_remote_missing() -> None:
    """First push (404 / not-found from LangSmith) creates the prompt."""
    client = MagicMock()
    client.pull_prompt.side_effect = Exception("404 not found")

    action = _push_one(
        client,
        "verdict-council/intake",
        "intake.md",
        dry_run=False,
    )
    assert action == "created"
    client.push_prompt.assert_called_once()


def test_push_one_dry_run_makes_no_api_calls_to_push() -> None:
    """`--dry-run` must not call `push_prompt`."""
    client = MagicMock()
    client.pull_prompt.side_effect = Exception("404 not found")

    action = _push_one(
        client,
        "verdict-council/intake",
        "intake.md",
        dry_run=True,
    )
    assert action == "created"
    client.push_prompt.assert_not_called()


def test_unrelated_pull_exception_propagates() -> None:
    """Auth errors must not be silently treated as 'remote missing'."""
    client = MagicMock()
    client.pull_prompt.side_effect = RuntimeError("Connection refused")

    with pytest.raises(RuntimeError, match="Connection refused"):
        _push_one(client, "verdict-council/intake", "intake.md", dry_run=False)
