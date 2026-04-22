"""Mesh-backed pipeline runner — orchestrates the 9-agent chain over Solace A2A.

Public surface mirrors `src/pipeline/runner.py::PipelineRunner` so the FastAPI
routes, WhatIfController, and eval scripts can swap one for the other:

    runner = MeshPipelineRunner(a2a_client=...)
    result = await runner.run(case_state)

Delegation model:

- Each agent lives as its own SAM process (see `configs/agents/*.yaml`).
  The runner publishes a JSON-RPC SendTaskRequest with the current
  CaseState as a DataPart to `verdictcouncil/a2a/v1/agent/request/<agent>`
  and awaits the SendTaskResponse on its own response wildcard.
- Agents 1, 2, 6, 7, 8, 9 run sequentially. Responses route back to
  `verdictcouncil/a2a/v1/agent/response/mesh-runner/<task_id>`.
- Agents 3, 4, 5 (L2) publish in parallel. Their responses route to
  the aggregator's wildcard (the runner sets replyTo accordingly), and
  the runner awaits the aggregator's merged SendTaskResponse on a
  dedicated task id `layer2-<case>-<run>`.

The runner owns no LLM logic: tool loops, schema validation, and
retry handling live inside the SAM agents themselves. The runner
only orchestrates, persists per-step checkpoints, and emits SSE
progress events.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

import redis.asyncio as redis
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.schemas.pipeline_events import PipelineProgressEvent
from src.db.pipeline_state import persist_case_state
from src.pipeline._a2a_client import (
    A2AClient,
    build_send_task_request,
    new_task_id,
)
from src.pipeline.hooks import (
    HookContext,
    HookResult,
    PipelineHook,
    default_hooks,
)
from src.pipeline.runner import AGENT_ORDER  # preserve pipeline order
from src.services.layer2_aggregator.a2a import parse_send_task_response
from src.services.pipeline_events import publish_progress
from src.shared.audit import append_audit_entry
from src.shared.case_state import CaseState, CaseStatusEnum
from src.shared.config import settings
from src.shared.validation import (
    FIELD_OWNERSHIP,
    FieldOwnershipError,
    validate_field_ownership,
)

logger = logging.getLogger(__name__)


# Logical grouping — drives serial vs. parallel dispatch.
L1_AGENTS: tuple[str, ...] = ("case-processing", "complexity-routing")
L2_AGENTS: tuple[str, ...] = (
    "evidence-analysis",
    "fact-reconstruction",
    "witness-analysis",
)
L3_AGENTS: tuple[str, ...] = (
    "legal-knowledge",
    "argument-construction",
    "deliberation",
    "governance-verdict",
)

# Maps each L2 agent's config name → the CaseState field it owns. Mirrors
# Layer2Aggregator.REQUIRED_AGENTS — aggregator and runner must agree.
L2_AGENT_KEY: dict[str, str] = {
    "evidence-analysis": "evidence_analysis",
    "fact-reconstruction": "extracted_facts",
    "witness-analysis": "witnesses",
}

DEFAULT_AGENT_TIMEOUT_SECONDS = 60.0
L2_BARRIER_TIMEOUT_SECONDS = 120.0  # matches aggregator.TIMEOUT_SECONDS
CORRELATION_TTL_SECONDS = 300

MESH_RUNNER_NAME = "mesh-runner"
AGGREGATOR_NAME = "layer2-aggregator"


def _request_topic(namespace: str, agent: str) -> str:
    return f"{namespace}/a2a/v1/agent/request/{agent}"


def _response_topic(namespace: str, delegating: str, task_id: str) -> str:
    return f"{namespace}/a2a/v1/agent/response/{delegating}/{task_id}"


class MeshPipelineRunner:
    """Distributed replacement for the sequential `PipelineRunner`.

    Preserves the guardrails, halts, and judge-KB hook from the
    sequential runner. Agent invocation is replaced with A2A publish +
    response await.
    """

    def __init__(
        self,
        a2a_client: A2AClient,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        client: AsyncOpenAI | None = None,
        redis_client: redis.Redis | None = None,
        namespace: str = "verdictcouncil",
        agent_timeout_seconds: float = DEFAULT_AGENT_TIMEOUT_SECONDS,
        hooks: list[PipelineHook] | None = None,
    ) -> None:
        self._a2a = a2a_client
        self._session_factory = session_factory
        self._client = client or AsyncOpenAI(api_key=settings.openai_api_key)
        self._redis = redis_client or redis.Redis.from_url(
            settings.redis_url, decode_responses=False
        )
        self._namespace = namespace.strip("/")
        self._agent_timeout = agent_timeout_seconds
        self._hooks: list[PipelineHook] = (
            hooks if hooks is not None else default_hooks(self._client)
        )

    # ------------------------------------------------------------------
    # Public API — matches PipelineRunner
    # ------------------------------------------------------------------

    async def run(
        self,
        case_state: CaseState,
        *,
        run_id: str | None = None,
    ) -> CaseState:
        """Run the full 9-agent pipeline over the mesh.

        `run_id` invariant: if provided, must equal `case_state.run_id` —
        the runner never mints a fresh id or mutates `state.run_id`. This
        keeps `latest_run_id` persistence (Phase 2.2) aligned with the
        checkpoint chain.
        """
        run_id = self._resolve_run_id(case_state, run_id)
        state = case_state
        ctx = HookContext(is_resume=False, run_id=run_id, case_id=str(state.case_id))
        current_agent = "input-guardrail"

        try:
            # before_run hooks — InputGuardrailHook fires here.
            state, halted = await self._run_before_run_hooks(state, ctx, run_id)
            if halted:
                return state

            # L1 — sequential agents 1 & 2.
            for agent_name in L1_AGENTS:
                current_agent = agent_name
                state = await self._invoke_agent_sequential(agent_name, state, run_id)
                await self._checkpoint(state, run_id, agent_name)
                state, halted = await self._run_after_agent_hooks(agent_name, state, ctx, run_id)
                if halted:
                    return state

            # L2 — parallel fan-out via the aggregator.
            current_agent = "layer2-aggregator"
            state = await self._invoke_l2_fanout(state, run_id)
            await self._checkpoint(state, run_id, "layer2-aggregator")

            # L3 — sequential agents 6–9.
            for agent_name in L3_AGENTS:
                current_agent = agent_name
                state = await self._invoke_agent_sequential(agent_name, state, run_id)
                await self._checkpoint(state, run_id, agent_name)
                state, halted = await self._run_after_agent_hooks(agent_name, state, ctx, run_id)
                if halted:
                    return state
        except TimeoutError as exc:
            # _invoke_l2_fanout re-raises TimeoutError without emitting its
            # own terminal; we preserve the specific halt reason here
            # instead of falling into the generic exception branch.
            reason = (
                "l2_barrier_timeout" if current_agent == "layer2-aggregator" else "agent_timeout"
            )
            await self._emit_terminal(
                state,
                reason=reason,
                stopped_at=current_agent,
                error=str(exc)[:500],
            )
            raise
        except Exception as exc:
            await self._emit_terminal(
                state,
                reason="exception",
                stopped_at=current_agent,
                error=str(exc)[:500],
            )
            raise

        logger.info(
            "Mesh pipeline completed for case_id=%s run_id=%s status=%s",
            state.case_id,
            run_id,
            state.status,
        )
        assert state.run_id == run_id, "run_id invariant broken at run() exit"
        return state

    async def run_from(
        self,
        case_state: CaseState,
        start_agent: str,
        *,
        run_id: str | None = None,
    ) -> CaseState:
        """Re-enter the mesh pipeline at `start_agent` and run downstream.

        Topology-aware dispatch:
            L1 agent      → L1[idx:] sequential + full L2 fanout + L3 sequential
            L2 agent      → full L2 fanout + L3 sequential (aggregator barrier
                            requires all three L2 agents to run as a unit)
            L3 agent      → L3[idx:] sequential

        Skips the input guardrail — the caller is re-running from a
        mid-pipeline state, not raw input. Downstream hooks (escalation
        halt, governance halts) still fire.

        `run_id` invariant: if provided, must equal `case_state.run_id`.
        """
        run_id = self._resolve_run_id(case_state, run_id)
        state = case_state
        ctx = HookContext(is_resume=True, run_id=run_id, case_id=str(state.case_id))
        current_agent = start_agent

        try:
            # before_run hooks — InputGuardrailHook no-ops when is_resume=True.
            state, halted = await self._run_before_run_hooks(state, ctx, run_id)
            if halted:
                return state

            if start_agent in L1_AGENTS:
                l1_start = L1_AGENTS.index(start_agent)
                for agent_name in L1_AGENTS[l1_start:]:
                    current_agent = agent_name
                    state = await self._invoke_agent_sequential(agent_name, state, run_id)
                    await self._checkpoint(state, run_id, agent_name)
                    state, halted = await self._run_after_agent_hooks(
                        agent_name, state, ctx, run_id
                    )
                    if halted:
                        return state
                current_agent = "layer2-aggregator"
                state = await self._invoke_l2_fanout(state, run_id)
                await self._checkpoint(state, run_id, AGGREGATOR_NAME)
                l3_start = 0
            elif start_agent in L2_AGENTS:
                current_agent = "layer2-aggregator"
                state = await self._invoke_l2_fanout(state, run_id)
                await self._checkpoint(state, run_id, AGGREGATOR_NAME)
                l3_start = 0
            elif start_agent in L3_AGENTS:
                l3_start = L3_AGENTS.index(start_agent)
            else:
                raise ValueError(
                    f"Unknown start_agent '{start_agent}'. "
                    f"Must be one of {L1_AGENTS + L2_AGENTS + L3_AGENTS}"
                )

            for agent_name in L3_AGENTS[l3_start:]:
                current_agent = agent_name
                state = await self._invoke_agent_sequential(agent_name, state, run_id)
                await self._checkpoint(state, run_id, agent_name)
                state, halted = await self._run_after_agent_hooks(agent_name, state, ctx, run_id)
                if halted:
                    return state
        except TimeoutError as exc:
            reason = (
                "l2_barrier_timeout" if current_agent == "layer2-aggregator" else "agent_timeout"
            )
            await self._emit_terminal(
                state,
                reason=reason,
                stopped_at=current_agent,
                error=str(exc)[:500],
            )
            raise
        except Exception as exc:
            await self._emit_terminal(
                state,
                reason="exception",
                stopped_at=current_agent,
                error=str(exc)[:500],
            )
            raise

        assert state.run_id == run_id, "run_id invariant broken at run_from() exit"
        return state

    # ------------------------------------------------------------------
    # Hook dispatch helpers
    # ------------------------------------------------------------------

    async def _run_before_run_hooks(
        self, state: CaseState, ctx: HookContext, run_id: str
    ) -> tuple[CaseState, bool]:
        for hook in self._hooks:
            result = await hook.before_run(state, ctx)
            state = result.state
            if result.halt:
                await self._emit_terminal(
                    state,
                    reason=result.reason,
                    stopped_at=result.stopped_at or "before_run",
                )
                return state, True
        return state, False

    async def _run_after_agent_hooks(
        self, agent_name: str, state: CaseState, ctx: HookContext, run_id: str
    ) -> tuple[CaseState, bool]:
        for hook in self._hooks:
            result = await hook.after_agent(agent_name, state, ctx)
            state = result.state
            if result.halt:
                assert state.run_id == run_id, "run_id invariant broken on halt"
                await self._emit_terminal(
                    state,
                    reason=result.reason,
                    stopped_at=result.stopped_at or agent_name,
                )
                return state, True
        return state, False

    # ------------------------------------------------------------------
    # Agent invocation — single-agent sequential path
    # ------------------------------------------------------------------

    async def _invoke_agent_sequential(
        self,
        agent_name: str,
        state: CaseState,
        run_id: str,
    ) -> CaseState:
        task_id = new_task_id(f"{agent_name}-{run_id[:8]}")
        reply_to = _response_topic(self._namespace, MESH_RUNNER_NAME, task_id)
        envelope = build_send_task_request(
            task_id=task_id,
            session_id=run_id,
            payload=state.model_dump(mode="json"),
            metadata={
                "case_id": str(state.case_id),
                "run_id": run_id,
                "agent_name": agent_name,
            },
        )
        request_topic = _request_topic(self._namespace, agent_name)

        await self._emit_progress(agent_name, state, "started")
        try:
            payload_hash = await self._a2a.publish(request_topic, envelope, reply_to=reply_to)
            response = await self._a2a.await_response(task_id, timeout=self._agent_timeout)
        except Exception as exc:
            await self._emit_progress(agent_name, state, "failed", error=str(exc)[:500])
            raise

        state = append_audit_entry(
            state,
            agent="a2a",
            action="agent_request_published",
            input_payload={
                "topic": request_topic,
                "task_id": task_id,
                "agent": agent_name,
                "payload_sha256": payload_hash,
            },
        )
        updated = self._parse_agent_response(response, state, agent_name)
        await self._emit_progress(agent_name, updated, "completed")
        return updated

    # ------------------------------------------------------------------
    # Agent invocation — L2 parallel fan-out via the aggregator
    # ------------------------------------------------------------------

    async def _invoke_l2_fanout(self, state: CaseState, run_id: str) -> CaseState:
        """Publish 3 L2 agent requests in parallel; await the aggregator's merged response.

        Stashes correlation + run meta in Redis so the aggregator can
        route and merge without in-band metadata pass-through.
        """
        case_id = str(state.case_id)
        mesh_task_id = f"layer2-{case_id}-{run_id}"
        mesh_reply_to = _response_topic(self._namespace, MESH_RUNNER_NAME, mesh_task_id)

        await self._stash_run_meta(case_id, run_id, state, mesh_reply_to)

        publish_coros = []
        publish_meta: list[dict[str, str]] = []
        for agent_name in L2_AGENTS:
            agent_key = L2_AGENT_KEY[agent_name]
            sub_task_id = new_task_id(f"{agent_name}-{run_id[:8]}")
            await self._stash_sub_task(sub_task_id, agent_key, case_id, run_id)
            agg_reply_to = _response_topic(self._namespace, AGGREGATOR_NAME, sub_task_id)
            envelope = build_send_task_request(
                task_id=sub_task_id,
                session_id=run_id,
                payload=state.model_dump(mode="json"),
                metadata={
                    "case_id": case_id,
                    "run_id": run_id,
                    "agent_name": agent_name,
                    "agent_key": agent_key,
                },
            )
            request_topic = _request_topic(self._namespace, agent_name)
            publish_coros.append(self._a2a.publish(request_topic, envelope, reply_to=agg_reply_to))
            publish_meta.append(
                {"topic": request_topic, "task_id": sub_task_id, "agent": agent_name}
            )
            await self._emit_progress(agent_name, state, "started")

        payload_hashes = await asyncio.gather(*publish_coros)
        for meta, payload_hash in zip(publish_meta, payload_hashes, strict=True):
            state = append_audit_entry(
                state,
                agent="a2a",
                action="agent_request_published",
                input_payload={**meta, "payload_sha256": payload_hash},
            )

        try:
            merged_response = await self._a2a.await_response(
                mesh_task_id, timeout=L2_BARRIER_TIMEOUT_SECONDS
            )
        except TimeoutError:
            # Per-L2 wire events stay here so each agent shows as failed in
            # the per-agent stream. The run-level terminal event is the
            # outer orchestrator's responsibility (run/run_from), so we
            # don't double-emit it from here — just re-raise.
            for agent_name in L2_AGENTS:
                await self._emit_progress(agent_name, state, "failed", error="L2 barrier timeout")
            raise

        merged_dict = parse_send_task_response(merged_response)
        if not merged_dict:
            for agent_name in L2_AGENTS:
                await self._emit_progress(
                    agent_name, state, "failed", error="Empty L2 merged response"
                )
            raise RuntimeError("L2 aggregator returned empty merged state")

        merged_state = CaseState.model_validate(merged_dict)
        for agent_name in L2_AGENTS:
            await self._emit_progress(agent_name, merged_state, "completed")
        return merged_state

    # ------------------------------------------------------------------
    # Redis correlation stashing (contract with layer2-aggregator)
    # ------------------------------------------------------------------

    async def _stash_run_meta(
        self,
        case_id: str,
        run_id: str,
        state: CaseState,
        mesh_reply_to: str,
    ) -> None:
        key = f"vc:aggregator:run:{case_id}:{run_id}:meta"
        payload = json.dumps(
            {
                "base_state": state.model_dump(mode="json"),
                "mesh_reply_to": mesh_reply_to,
            }
        )
        await self._redis.set(key, payload, ex=CORRELATION_TTL_SECONDS)

    async def _stash_sub_task(
        self,
        sub_task_id: str,
        agent_key: str,
        case_id: str,
        run_id: str,
    ) -> None:
        key = f"vc:aggregator:sub_task:{sub_task_id}"
        await self._redis.set(
            key,
            f"{agent_key}|{case_id}|{run_id}",
            ex=CORRELATION_TTL_SECONDS,
        )

    # ------------------------------------------------------------------
    # Response parsing + progress emission
    # ------------------------------------------------------------------

    def _parse_agent_response(
        self,
        envelope: dict,
        prior_state: CaseState,
        agent_name: str,
    ) -> CaseState:
        """Parse a SendTaskResponse DataPart into a CaseState.

        Assumes agents return the full updated CaseState (dict) as a
        DataPart. If an agent returns a fragment instead, we fall back
        to merging the fragment onto `prior_state`. In both cases the
        result is checked against `FIELD_OWNERSHIP`; unauthorized writes
        are stripped rather than honored.
        """
        payload = parse_send_task_response(envelope)
        if not payload:
            raise RuntimeError(f"Empty/unparseable response from agent {agent_name!r}")

        original_dict = prior_state.model_dump(mode="json")
        merged_dict = payload if "case_id" in payload else {**original_dict, **payload}

        try:
            validate_field_ownership(agent_name, original_dict, merged_dict)
        except FieldOwnershipError as exc:
            logger.warning(
                "Field ownership violation by '%s': %s. Stripping unauthorized fields.",
                agent_name,
                exc,
            )
            allowed = FIELD_OWNERSHIP.get(agent_name, set())
            stripped = {**original_dict}
            for key in allowed:
                if key in payload:
                    stripped[key] = payload[key]
            merged_dict = stripped

        parsed = CaseState.model_validate(merged_dict)
        # Emit the audit entry downstream consumers filter on.
        # `routes/judge.py:367` and `routes/case_data.py:93` both require
        # `action="agent_response"` with a populated `output_payload`.
        # Mirror the shape at `src/pipeline/runner.py:615-627` — mesh has
        # no tool-call log or token accounting, so those are None.
        return append_audit_entry(
            parsed,
            agent=agent_name,
            action="agent_response",
            input_payload={"state_keys": list(original_dict.keys())},
            output_payload=payload,
            tool_calls=None,
            token_usage=None,
            model=None,
        )

    async def _emit_progress(
        self,
        agent_name: str,
        state: CaseState,
        phase: str,
        *,
        error: str | None = None,
    ) -> None:
        try:
            step = AGENT_ORDER.index(agent_name) + 1
        except ValueError:
            return
        event = PipelineProgressEvent(
            case_id=state.case_id,
            agent=agent_name,
            phase=phase,  # type: ignore[arg-type]
            step=step,
            total=len(AGENT_ORDER),
            ts=datetime.now(UTC),
            error=error,
        )
        await publish_progress(event)

    async def _emit_terminal(
        self,
        state: CaseState,
        *,
        reason: str,
        stopped_at: str,
        error: str | None = None,
    ) -> None:
        """Emit the run-level terminal SSE event.

        One event per halt path; ``agent="pipeline"`` + ``phase="terminal"``
        is the subscriber's authoritative close signal. ``detail`` carries
        the stage at which the halt occurred so downstream analytics can
        attribute it correctly without mislabelling every halt as a
        governance-verdict failure.
        """
        event = PipelineProgressEvent(
            case_id=state.case_id,
            agent="pipeline",
            phase="terminal",
            step=None,
            total=len(AGENT_ORDER),
            ts=datetime.now(UTC),
            error=error,
            detail={"reason": reason, "stopped_at": stopped_at},
        )
        await publish_progress(event)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _checkpoint(
        self,
        state: CaseState,
        run_id: str,
        agent_name: str,
    ) -> None:
        """Persist a per-agent checkpoint via a short-lived session.

        An AsyncSession cannot span the full 9-agent mesh run (9 network
        hops, minutes of wall clock). Each checkpoint opens its own
        transaction via `self._session_factory` so the connection pool
        is never held across an A2A await.
        """
        async with self._session_factory() as session:
            await persist_case_state(
                session,
                case_id=state.case_id,
                run_id=run_id,
                agent_name=agent_name,
                state=state,
            )

    @staticmethod
    def _resolve_run_id(state: CaseState, run_id: str | None) -> str:
        """Enforce the `run_id` invariant.

        Rules:
        - If caller passes `run_id=None`, default to `state.run_id`.
        - If caller passes a mismatched `run_id`, raise ValueError —
          the caller must hand in a coherent (state, run_id) pair.
        The runner never mints a fresh id or mutates `state.run_id`.
        """
        effective = run_id if run_id is not None else state.run_id
        if state.run_id != effective:
            raise ValueError(
                f"run_id invariant violated: state.run_id={state.run_id!r} "
                f"but run_id arg={effective!r}. Callers must pass a coherent pair."
            )
        return effective
