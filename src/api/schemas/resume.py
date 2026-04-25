"""Sprint 4 4.A3.15 — unified gate-resume payload.

Single contract for the four judge actions at any gate:

- ``advance``  — continue forward to the next phase.
- ``rerun``    — re-execute the previous phase (optionally a single
  research subagent for gate2). ``field_corrections`` mutates state
  slots inline (e.g. gate3 judicial-question edits).
- ``halt``     — terminate the run; routes to the ``terminal`` node.
- ``send_back`` — rewind the LangGraph thread to a past phase
  checkpoint (4.A3.14). Same ``thread_id``; later checkpoints become
  stale-but-visible-via-``get_state_history`` for audit.

The frontend ``<GateReviewPanel>`` (4.C5b.1/2) targets this schema
directly via the unified ``POST /cases/{id}/respond`` endpoint
(4.A3.15). The TS-side ``ResumePayload`` type must mirror this exactly.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResumePayload(BaseModel):
    """Judge response at any of the four pipeline gates.

    Field combinations are validated by ``_check_action_fields``:
    - ``rerun`` may carry ``phase`` (and ``subagent`` only when
      ``phase == "research"``) plus optional ``field_corrections``.
    - ``send_back`` requires ``to_phase`` and forbids the rerun-only
      fields.
    - ``advance`` and ``halt`` accept only ``notes``.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["advance", "rerun", "halt", "send_back"]
    notes: str | None = Field(
        None, description="Free-text judge note (audit-logged on every action)."
    )

    # rerun-only
    phase: Literal["intake", "research", "synthesis", "audit"] | None = Field(
        None,
        description="Target phase for rerun. Required when action='rerun'.",
    )
    subagent: Literal["evidence", "facts", "witnesses", "law"] | None = Field(
        None,
        description=(
            "Single research subagent to re-run when phase='research'. "
            "Forbidden when phase != 'research'."
        ),
    )
    field_corrections: dict[str, Any] | None = Field(
        None,
        description=(
            "GraphState slot updates applied atomically with a rerun. "
            "Used by gate3 for inline judicial-question edits."
        ),
    )

    # send_back-only
    to_phase: Literal["intake", "research", "synthesis"] | None = Field(
        None,
        description=(
            "Target phase to rewind to. Required when action='send_back'. "
            "'audit' is excluded — sending back to audit is a rerun-audit, "
            "not a rewind."
        ),
    )

    @model_validator(mode="after")
    def _check_action_fields(self) -> ResumePayload:
        action = self.action

        rerun_only = (self.phase, self.subagent, self.field_corrections)
        send_back_only = (self.to_phase,)

        if action == "advance" and any(rerun_only) or action == "advance" and any(send_back_only):
            raise ValueError(
                "action='advance' must not carry phase/subagent/field_corrections/to_phase"
            )
        if action == "halt" and any(rerun_only) or action == "halt" and any(send_back_only):
            raise ValueError(
                "action='halt' must not carry phase/subagent/field_corrections/to_phase"
            )
        if action == "rerun":
            if self.phase is None:
                raise ValueError("action='rerun' requires 'phase'")
            if any(send_back_only):
                raise ValueError("action='rerun' must not carry 'to_phase'")
            if self.subagent is not None and self.phase != "research":
                raise ValueError("'subagent' is only valid when phase='research'")
        if action == "send_back":
            if self.to_phase is None:
                raise ValueError("action='send_back' requires 'to_phase'")
            if any(rerun_only):
                raise ValueError(
                    "action='send_back' must not carry phase/subagent/field_corrections"
                )

        return self
