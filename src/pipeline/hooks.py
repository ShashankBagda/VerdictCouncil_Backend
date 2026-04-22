"""Pipeline hook protocol and standard hook implementations.

Hooks are the extension point for cross-cutting concerns that fire
before/after the pipeline runs or after each individual agent step.
The halt semantics are captured in HookResult so callers can short-circuit
without embedding stop logic inside the runner loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from src.pipeline.guardrails import check_input_injection, validate_output_integrity
from src.shared.audit import append_audit_entry
from src.shared.case_state import CaseState, CaseStatusEnum

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HookResult:
    state: CaseState
    halt: bool = False
    reason: str | None = None
    stopped_at: str | None = None


@dataclass(frozen=True)
class HookContext:
    is_resume: bool
    run_id: str
    case_id: str


class PipelineHook(Protocol):
    async def before_run(self, state: CaseState, ctx: HookContext) -> HookResult: ...

    async def after_agent(
        self, agent_name: str, state: CaseState, ctx: HookContext
    ) -> HookResult: ...

    async def after_run(self, state: CaseState, ctx: HookContext) -> HookResult: ...


class InputGuardrailHook:
    """Blocks prompt injection in case description before the pipeline runs.

    Skipped on resume (ctx.is_resume=True) — the guardrail already ran on the
    original submit; re-running would double-charge an OpenAI call and could
    block a What-If scenario on content the user did not re-submit.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    async def before_run(self, state: CaseState, ctx: HookContext) -> HookResult:
        if ctx.is_resume:
            return HookResult(state=state)
        description = state.case_metadata.get("description", "") if state.case_metadata else ""
        if not description:
            return HookResult(state=state)
        result = await check_input_injection(description, self._client)
        if not result.get("blocked"):
            return HookResult(state=state)
        logger.warning(
            "Input injection detected (method=%s, case_id=%s): %s",
            result["method"],
            ctx.case_id,
            result["reason"],
        )
        state.case_metadata["description"] = result["sanitized_text"]
        state = append_audit_entry(
            state,
            agent="guardrails",
            action="input_injection_blocked",
            input_payload={"method": result["method"]},
            output_payload={"reason": result["reason"]},
        )
        return HookResult(state=state)

    async def after_agent(
        self, agent_name: str, state: CaseState, ctx: HookContext
    ) -> HookResult:
        return HookResult(state=state)

    async def after_run(self, state: CaseState, ctx: HookContext) -> HookResult:
        return HookResult(state=state)


class ComplexityEscalationHook:
    """Halts the pipeline when complexity-routing escalates the case."""

    async def before_run(self, state: CaseState, ctx: HookContext) -> HookResult:
        return HookResult(state=state)

    async def after_agent(
        self, agent_name: str, state: CaseState, ctx: HookContext
    ) -> HookResult:
        if agent_name != "complexity-routing":
            return HookResult(state=state)
        if state.status == CaseStatusEnum.escalated:
            logger.warning(
                "Pipeline halted at complexity-routing: case escalated (case_id=%s)",
                ctx.case_id,
            )
            return HookResult(
                state=state,
                halt=True,
                reason="complexity_escalation",
                stopped_at="complexity-routing",
            )
        return HookResult(state=state)

    async def after_run(self, state: CaseState, ctx: HookContext) -> HookResult:
        return HookResult(state=state)


class GovernanceHaltHook:
    """Halts the pipeline on output integrity failure or critical fairness issues."""

    async def before_run(self, state: CaseState, ctx: HookContext) -> HookResult:
        return HookResult(state=state)

    async def after_agent(
        self, agent_name: str, state: CaseState, ctx: HookContext
    ) -> HookResult:
        if agent_name != "governance-verdict":
            return HookResult(state=state)
        integrity = validate_output_integrity(state.model_dump())
        if not integrity["passed"]:
            logger.error(
                "Output integrity check FAILED (case_id=%s): %s",
                ctx.case_id,
                integrity["issues"],
            )
            state = append_audit_entry(
                state,
                agent="guardrails",
                action="output_integrity_failed",
                output_payload=integrity,
            )
            state.status = CaseStatusEnum.escalated
            return HookResult(
                state=state,
                halt=True,
                reason="governance_halt",
                stopped_at="governance-verdict",
            )
        if state.fairness_check and state.fairness_check.critical_issues_found:
            logger.warning(
                "Pipeline halted: critical fairness issues (case_id=%s)",
                ctx.case_id,
            )
            state = state.model_copy(update={"status": CaseStatusEnum.escalated})
            return HookResult(
                state=state,
                halt=True,
                reason="governance_halt",
                stopped_at="governance-verdict",
            )
        return HookResult(state=state)

    async def after_run(self, state: CaseState, ctx: HookContext) -> HookResult:
        return HookResult(state=state)


def default_hooks(client: Any) -> list[PipelineHook]:
    """Return the standard hook list for production use."""
    return [
        InputGuardrailHook(client),
        ComplexityEscalationHook(),
        GovernanceHaltHook(),
    ]
