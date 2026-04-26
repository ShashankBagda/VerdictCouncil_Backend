"""Phase 5 — prompt/tool parity contract test.

For every phase prompt loaded via `prompt_registry.get_prompt(phase)`,
assert that each tool name the factory binds (`PHASE_TOOL_NAMES[phase]` /
`RESEARCH_TOOL_NAMES[scope]`) appears at least once in the prompt body.

This is a regression guard, not an audit-finding generator: the prompts
under `prompts/*.md` are already coherent with the factory bindings as of
the Phase 5 realignment PR. If a future change adds, removes, or renames a
tool in the factory's policy dicts without updating the matching prompt,
this test fails and forces the prompt edit to land in the same PR.

Aliases: `search_legal_rules` resolves to `search_domain_guidance` at
runtime (`_TOOL_ALIASES` in `agents/factory.py`). Either name is accepted.

Retired-tool mentions (e.g. "the legacy `cross_reference` tool was
retired in the topology rewrite") are intentionally NOT flagged here.
"""

from __future__ import annotations

import os

import pytest

from src.pipeline.graph.agents.factory import (
    PHASE_TOOL_NAMES,
    RESEARCH_TOOL_NAMES,
    _TOOL_ALIASES,
)
from src.pipeline.graph.prompt_registry import PHASE_TO_PROMPT_ID, get_prompt


# Map factory phase/scope keys → registry phase keys so the test reads
# the same prompt the factory does. PHASE_TOOL_NAMES uses bare phase
# names (`intake`, `synthesis`, `audit`); RESEARCH_TOOL_NAMES uses
# scope keys (`evidence`, `facts`, `witnesses`, `law`) which the
# registry exposes as `research-<scope>`.
_REGISTRY_KEY: dict[str, str] = {
    "intake": "intake",
    "synthesis": "synthesis",
    "audit": "audit",
    "evidence": "research-evidence",
    "facts": "research-facts",
    "witnesses": "research-witnesses",
    "law": "research-law",
}


def _aliases_for(name: str) -> set[str]:
    """Return the set of names that satisfy a binding for `name`.

    `search_legal_rules` is the new-topology canonical name; the
    underlying registered tool is still `search_domain_guidance`. Either
    appearing in the prompt counts as the agent being told it has the
    capability.
    """
    forms = {name}
    if name in _TOOL_ALIASES:
        forms.add(_TOOL_ALIASES[name])
    for canonical, actual in _TOOL_ALIASES.items():
        if actual == name:
            forms.add(canonical)
    return forms


def _force_local_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the test to the on-disk prompt file, not whatever LangSmith
    happens to have right now.

    The contract we're guarding is: the local fallback prompt and the
    factory bindings agree. Pointing at LangSmith would couple CI to
    network state and to whichever commit a teammate last pushed.
    """
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    # Bust any cached LangSmith pull captured before the env was cleared.
    from src.pipeline.graph.prompt_registry import _resolve_from_langsmith

    _resolve_from_langsmith.cache_clear()


def _allowed_tools_iter():
    """Yield (registry_phase_key, allowed_tool_name) pairs to parametrize."""
    for phase, tools in PHASE_TOOL_NAMES.items():
        for tool in tools:
            yield _REGISTRY_KEY[phase], tool
    for scope, tools in RESEARCH_TOOL_NAMES.items():
        for tool in tools:
            yield _REGISTRY_KEY[scope], tool


@pytest.mark.parametrize("phase, tool", list(_allowed_tools_iter()))
def test_prompt_mentions_each_allowed_tool(
    phase: str, tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_local_fallback(monkeypatch)
    prompt = get_prompt(phase)
    accepted = _aliases_for(tool)
    assert any(form in prompt for form in accepted), (
        f"Prompt {phase!r} does not mention any form of {tool!r} "
        f"({sorted(accepted)}). Either the prompt drifted from the factory "
        f"binding or the binding drifted from the prompt — fix one to match "
        f"the other."
    )


def test_every_factory_phase_has_a_registry_entry() -> None:
    """Guards the registry-key map above against silent drift."""
    factory_keys = set(PHASE_TOOL_NAMES) | set(RESEARCH_TOOL_NAMES)
    mapped = {_REGISTRY_KEY[k] for k in factory_keys}
    assert mapped <= set(PHASE_TO_PROMPT_ID), (
        f"Factory keys {sorted(factory_keys)} map to registry keys "
        f"{sorted(mapped)} but PHASE_TO_PROMPT_ID only knows "
        f"{sorted(PHASE_TO_PROMPT_ID)}. Update prompt_registry.py."
    )


def test_audit_phase_has_no_tool_assertions() -> None:
    """Audit independence: PHASE_TOOL_NAMES['audit'] must stay empty so this
    parity test makes no positive assertions about the audit prompt's tool
    list. Recorded as a test so a future change that adds a tool to audit
    forces an explicit decision.
    """
    assert PHASE_TOOL_NAMES["audit"] == [], (
        "Audit phase is supposed to have no tools (architecture decision "
        "A3 in the streaming plan + Sprint 0.5 §5 D-4 strict mode). "
        "Adding a tool here is a load-bearing change — review it."
    )
