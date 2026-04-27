"""Sprint 1 1.C3a.5 — unit tests for `prompt_registry.get_prompt`.

Covers:

- Cold lookup hits LangSmith.
- Cache hit avoids LangSmith on the second call.
- Judge-correction path calls `push_prompt` exactly once and busts the
  cache so subsequent reads see the new commit.
- LangSmith failure (no key, exception) falls back to the local
  `prompts/<file>.md` and logs a warning.

`langsmith.Client` is monkey-patched throughout — no network calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.prompts import ChatPromptTemplate

from src.pipeline.graph import prompt_registry


@pytest.fixture(autouse=True)
def _clear_cache_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the lru_cache and force LANGSMITH_API_KEY before every test."""
    prompt_registry._resolve_from_langsmith.cache_clear()
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")


def _fake_template(content: str) -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([("system", content)])


def _patch_client_factory(monkeypatch: pytest.MonkeyPatch, client: MagicMock) -> None:
    monkeypatch.setattr(prompt_registry, "_make_client", lambda: client)


# ---------------------------------------------------------------------------
# Cold lookup
# ---------------------------------------------------------------------------


def test_cold_lookup_pulls_from_langsmith(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.pull_prompt.return_value = _fake_template("INTAKE FROM LANGSMITH")
    _patch_client_factory(monkeypatch, client)

    result = prompt_registry.get_prompt("intake")

    assert result == "INTAKE FROM LANGSMITH"
    client.pull_prompt.assert_called_once_with("vc-intake")


# ---------------------------------------------------------------------------
# Cache hit
# ---------------------------------------------------------------------------


def test_second_lookup_hits_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.pull_prompt.return_value = _fake_template("CACHED")
    _patch_client_factory(monkeypatch, client)

    first = prompt_registry.get_prompt("synthesis")
    second = prompt_registry.get_prompt("synthesis")

    assert first == second == "CACHED"
    # Cache is keyed on the prompt_id, so the SDK call must happen exactly once.
    client.pull_prompt.assert_called_once_with("vc-synthesis")


# ---------------------------------------------------------------------------
# Judge correction → push + cache bust
# ---------------------------------------------------------------------------


def test_judge_correction_pushes_new_commit_and_busts_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.pull_prompt.return_value = _fake_template("ORIGINAL")
    _patch_client_factory(monkeypatch, client)

    # Warm the cache.
    prompt_registry.get_prompt("audit")
    assert client.pull_prompt.call_count == 1

    # Apply a correction.
    corrected = prompt_registry.get_prompt("audit", corrections="Add a fairness check.")

    assert "ORIGINAL" in corrected and "Add a fairness check." in corrected
    client.push_prompt.assert_called_once()
    args, kwargs = client.push_prompt.call_args
    assert args[0] == "vc-audit", "push must use the bare LangSmith id"
    assert kwargs.get("commit_tags") == ["judge-correction"]

    # The cache must be cleared so the next read goes back to LangSmith.
    client.pull_prompt.return_value = _fake_template("CORRECTED COMMIT")
    next_read = prompt_registry.get_prompt("audit")
    assert next_read == "CORRECTED COMMIT"
    assert client.pull_prompt.call_count == 2


# ---------------------------------------------------------------------------
# Local fallback
# ---------------------------------------------------------------------------


def test_falls_back_to_local_when_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No LANGSMITH_API_KEY → skip LangSmith, read prompts/<name>.md."""
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)

    result = prompt_registry.get_prompt("intake")

    assert "Intake Agent" in result, (
        "Local fallback must read prompts/intake.md (which begins with the "
        "'Intake Agent' heading); got something else, so the fallback path "
        "is not engaged."
    )


def test_falls_back_to_local_when_pull_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LangSmith exception (auth, network) → local fallback, not crash."""
    client = MagicMock()
    client.pull_prompt.side_effect = RuntimeError("Connection refused")
    _patch_client_factory(monkeypatch, client)

    result = prompt_registry.get_prompt("research-law")

    assert "Law Research Subagent" in result, (
        "Local fallback must read prompts/research-law.md when LangSmith "
        "throws; got unexpected content."
    )


# ---------------------------------------------------------------------------
# Surface contract
# ---------------------------------------------------------------------------


def test_unknown_phase_raises_keyerror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    with pytest.raises(KeyError, match="legacy-evidence-analysis"):
        prompt_registry.get_prompt("legacy-evidence-analysis")


def test_phase_to_prompt_id_covers_all_seven_phases() -> None:
    """Sprint 1 6-phase topology has 7 prompts (1 intake + 4 research + synthesis + audit)."""
    expected = {
        "intake",
        "research-evidence",
        "research-facts",
        "research-witnesses",
        "research-law",
        "synthesis",
        "audit",
    }
    assert set(prompt_registry.PHASE_TO_PROMPT_ID) == expected
    assert set(prompt_registry.PHASE_TO_FILE) == expected
