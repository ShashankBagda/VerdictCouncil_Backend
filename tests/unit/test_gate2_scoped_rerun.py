"""Phase 2 — gate-2 scoped rerun targeting fix.

Covers two failure modes that combine to silently widen "rerun this
one" into "rerun all four":

1. ``build_resume_payload`` only threads the ``subagent`` target
   through when ``notes`` is also set. A no-instructions rerun of a
   single subagent therefore loses the targeting signal entirely.
2. ``route_to_research_subagents`` unconditionally fans out to all
   four research scopes. Even if (1) were fixed, the router has no
   path to honour the targeting.

The fix re-uses ``state["extra_instructions"]``: the resume payload
keys an empty string by the target scope when no notes are supplied,
and the router treats the presence of any research-scope key in
``extra_instructions`` as the signal to scope the fan-out to those
keys. Empty string is falsy in the factory's correction-application
path (``if not corrections: return base``) so the prompt is unchanged
when there are no notes to apply.

See ``tasks/ticket-2026-04-26-gate2-per-agent-rerun-targeting.md`` for
the user-facing repro.
"""

from __future__ import annotations

import pytest

from src.pipeline.graph.research import (
    RESEARCH_SCOPES,
    RESEARCH_SUBAGENT_NODES,
    route_to_research_subagents,
)
from src.pipeline.graph.resume import build_resume_payload


# ---------------------------------------------------------------------------
# Resume payload — subagent must be threaded even when notes is empty
# ---------------------------------------------------------------------------


def test_rerun_with_subagent_and_notes_keys_notes_by_scope() -> None:
    """Pre-existing happy path — both signals present.

    The resume payload should map the corrective note onto the target
    scope so ``factory.py`` picks it up via
    ``state["extra_instructions"][scope]``. This already worked before
    the fix; keep the test so the regression-safety net catches a
    future refactor that drops this behaviour.
    """
    payload = {
        "resume_action": "rerun",
        "subagent": "evidence",
        "notes": "re-pull evidence",
    }
    resume = build_resume_payload(payload)
    assert resume == {
        "action": "rerun",
        "notes": {"evidence": "re-pull evidence"},
    }


def test_rerun_with_subagent_only_still_scopes_target() -> None:
    """Bug fix — no-notes rerun of a single subagent must NOT silently
    widen to all four scopes.

    Before the fix, a payload with ``subagent`` but no ``notes`` lost
    the targeting signal: the resume dict carried no scoping signal,
    the gate-apply node wrote nothing into ``extra_instructions``, and
    the research router fanned out to all four scopes — overwriting
    the three subagents the judge did not target.

    After the fix, the resume payload encodes the target scope as an
    empty-string instruction. The empty string is intentional: the
    factory's corrective-instruction path uses ``if not corrections``
    so an empty string is a no-op for the prompt while remaining a
    truthy "key present" signal for the research router.
    """
    payload = {"resume_action": "rerun", "subagent": "facts"}
    resume = build_resume_payload(payload)
    assert resume["action"] == "rerun"
    assert resume["notes"] == {"facts": ""}, (
        "subagent without notes must still produce a scope-keyed "
        "extra_instructions entry so the router can honour the targeting"
    )


def test_rerun_without_subagent_or_notes_has_no_extra_instructions() -> None:
    """Legacy 'rerun all four' path — no scoping signal, no notes."""
    payload = {"resume_action": "rerun"}
    resume = build_resume_payload(payload)
    assert resume == {"action": "rerun"}


def test_rerun_with_bare_string_notes_remains_bare() -> None:
    """Gate-level (not subagent-level) corrective notes still flow as a
    bare string. ``make_gate_apply`` keys this by gate name."""
    payload = {"resume_action": "rerun", "notes": "redo whole research"}
    resume = build_resume_payload(payload)
    assert resume == {"action": "rerun", "notes": "redo whole research"}


# ---------------------------------------------------------------------------
# Research router — fan-out must respect the targeting signal
# ---------------------------------------------------------------------------


def _state(extra: dict[str, str] | None) -> dict:
    """Minimal state shape the router reads."""
    return {
        "case": object(),  # opaque — the router doesn't inspect it
        "extra_instructions": extra or {},
    }


def test_router_full_fanout_when_no_extra_instructions() -> None:
    sends = route_to_research_subagents(_state(None))
    assert sorted(s.node for s in sends) == sorted(
        RESEARCH_SUBAGENT_NODES[scope] for scope in RESEARCH_SCOPES
    )


def test_router_full_fanout_when_extra_instructions_keyed_by_gate() -> None:
    """Legacy ``{gate2: ...}`` shape must NOT be interpreted as scoping —
    it predates the per-subagent path and means 'apply this note across
    the whole gate's rerun'."""
    sends = route_to_research_subagents(_state({"gate2": "redo whole research"}))
    assert sorted(s.node for s in sends) == sorted(
        RESEARCH_SUBAGENT_NODES[scope] for scope in RESEARCH_SCOPES
    )


@pytest.mark.parametrize("scope", list(RESEARCH_SCOPES))
def test_router_scoped_fanout_when_single_scope_keyed(scope: str) -> None:
    """Bug fix — when ``extra_instructions`` carries a research-scope
    key, the router must fan out only to that scope."""
    sends = route_to_research_subagents(_state({scope: ""}))
    assert len(sends) == 1, (
        f"Scoped rerun of {scope!r} must produce exactly one Send; "
        f"got {len(sends)} ({[s.node for s in sends]})"
    )
    assert sends[0].node == RESEARCH_SUBAGENT_NODES[scope]


def test_router_scoped_fanout_with_corrective_note_present() -> None:
    """When the keyed entry is a non-empty corrective note (i.e. judge
    supplied instructions), the router must still scope correctly —
    the targeting comes from the key, not the value."""
    sends = route_to_research_subagents(_state({"law": "Cite [2018] SGCA 12"}))
    assert [s.node for s in sends] == [RESEARCH_SUBAGENT_NODES["law"]]


def test_router_scoped_fanout_with_multiple_scopes() -> None:
    """If the judge somehow targets two scopes at once (e.g. via a future
    bulk-rerun UI), the router fans out to exactly those scopes — not
    all four."""
    sends = route_to_research_subagents(_state({"evidence": "", "facts": ""}))
    assert sorted(s.node for s in sends) == sorted(
        [RESEARCH_SUBAGENT_NODES["evidence"], RESEARCH_SUBAGENT_NODES["facts"]]
    )
