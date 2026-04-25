"""Sprint 4 4.A3.15 — ResumePayload contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.schemas.resume import ResumePayload


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_advance_minimal() -> None:
    p = ResumePayload(action="advance")
    assert p.action == "advance"
    assert p.notes is None


def test_advance_with_notes() -> None:
    p = ResumePayload(action="advance", notes="ok by me")
    assert p.notes == "ok by me"


def test_halt_with_notes() -> None:
    p = ResumePayload(action="halt", notes="judge halted")
    assert p.action == "halt"


def test_rerun_phase_intake() -> None:
    p = ResumePayload(action="rerun", phase="intake", notes="bad domain")
    assert p.phase == "intake"


def test_rerun_research_with_subagent() -> None:
    p = ResumePayload(
        action="rerun",
        phase="research",
        subagent="evidence",
        notes="re-pull evidence",
    )
    assert p.phase == "research"
    assert p.subagent == "evidence"


def test_rerun_synthesis_with_field_corrections() -> None:
    p = ResumePayload(
        action="rerun",
        phase="synthesis",
        field_corrections={"synthesis_output": {"judicial_questions": ["Q1?"]}},
    )
    assert p.field_corrections == {"synthesis_output": {"judicial_questions": ["Q1?"]}}


def test_send_back_to_research() -> None:
    p = ResumePayload(
        action="send_back",
        to_phase="research",
        notes="evidence weight matrix off",
    )
    assert p.to_phase == "research"


# ---------------------------------------------------------------------------
# extra="forbid"
# ---------------------------------------------------------------------------


def test_extra_forbid_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError) as exc:
        ResumePayload(action="advance", agent="evidence-analysis")  # type: ignore[call-arg]
    assert "Extra inputs are not permitted" in str(exc.value) or "extra" in str(exc.value)


# ---------------------------------------------------------------------------
# Action / field invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"action": "advance", "phase": "intake"},
        {"action": "advance", "subagent": "evidence"},
        {"action": "advance", "to_phase": "intake"},
        {"action": "advance", "field_corrections": {"x": 1}},
    ],
)
def test_advance_rejects_other_action_fields(kwargs) -> None:
    with pytest.raises(ValidationError):
        ResumePayload(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"action": "halt", "phase": "intake"},
        {"action": "halt", "to_phase": "intake"},
        {"action": "halt", "field_corrections": {"x": 1}},
    ],
)
def test_halt_rejects_other_action_fields(kwargs) -> None:
    with pytest.raises(ValidationError):
        ResumePayload(**kwargs)


def test_rerun_requires_phase() -> None:
    with pytest.raises(ValidationError) as exc:
        ResumePayload(action="rerun")
    assert "phase" in str(exc.value).lower()


def test_rerun_subagent_only_valid_for_research() -> None:
    with pytest.raises(ValidationError) as exc:
        ResumePayload(action="rerun", phase="synthesis", subagent="evidence")
    assert "subagent" in str(exc.value).lower()


def test_rerun_rejects_to_phase() -> None:
    with pytest.raises(ValidationError):
        ResumePayload(action="rerun", phase="intake", to_phase="intake")


def test_send_back_requires_to_phase() -> None:
    with pytest.raises(ValidationError) as exc:
        ResumePayload(action="send_back")
    assert "to_phase" in str(exc.value).lower()


def test_send_back_rejects_rerun_fields() -> None:
    with pytest.raises(ValidationError):
        ResumePayload(action="send_back", to_phase="research", phase="research")
    with pytest.raises(ValidationError):
        ResumePayload(action="send_back", to_phase="research", subagent="evidence")
    with pytest.raises(ValidationError):
        ResumePayload(
            action="send_back",
            to_phase="research",
            field_corrections={"x": 1},
        )


def test_send_back_audit_not_a_target() -> None:
    """'audit' is excluded from to_phase — re-running audit is a rerun, not a rewind."""
    with pytest.raises(ValidationError):
        ResumePayload(action="send_back", to_phase="audit")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Unknown actions rejected by Literal
# ---------------------------------------------------------------------------


def test_unknown_action_rejected() -> None:
    with pytest.raises(ValidationError):
        ResumePayload(action="cancel")  # type: ignore[arg-type]
