"""Mesh-backed pipeline runner — orchestrates the 9-agent chain over Solace A2A.

Public surface mirrors `src/pipeline/runner.py::PipelineRunner` so the FastAPI
routes, WhatIfController, and eval scripts can swap one for the other:

    runner = MeshPipelineRunner(a2a_client=...)
    result = await runner.run(case_state, judge_vector_store_id=...)

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
import uuid
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis
from openai import AsyncOpenAI

from src.api.schemas.pipeline_events import PipelineProgressEvent
from src.db.pipeline_state import persist_case_state
from src.pipeline._a2a_client import (
    A2AClient,
    build_send_task_request,
    new_task_id,
)
from src.pipeline.guardrails import check_input_injection, validate_output_integrity
from src.pipeline.runner import AGENT_ORDER  # preserve pipeline order
from src.services.layer2_aggregator.a2a import parse_send_task_response
from src.services.pipeline_events import publish_progress
from src.shared.audit import append_audit_entry
from src.shared.case_state import CaseState, CaseStatusEnum
from src.shared.config import settings

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
        client: AsyncOpenAI | None = None,
        redis_client: redis.Redis | None = None,
        namespace: str = "verdictcouncil",
        agent_timeout_seconds: float = DEFAULT_AGENT_TIMEOUT_SECONDS,
    ) -> None:
        self._a2a = a2a_client
        self._client = client or AsyncOpenAI(api_key=settings.openai_api_key)
        self._redis = redis_client or redis.Redis.from_url(
            settings.redis_url, decode_responses=False
        )
        self._namespace = namespace.strip("/")
        self._agent_timeout = agent_timeout_seconds

    # ------------------------------------------------------------------
    # Public API — matches PipelineRunner
    # ------------------------------------------------------------------

    async def run(
        self,
        case_state: CaseState,
        judge_vector_store_id: str | None = None,
        *,
        db: Any = None,
        run_id: str | None = None,
    ) -> CaseState:
        """Run the full 9-agent pipeline over the mesh."""
        state = case_state
        run_id = run_id or uuid.uuid4().hex

        state = await self._apply_input_guardrail(state)

        # L1 — sequential agents 1 & 2 with the escalation halt after 2.
        for agent_name in L1_AGENTS:
            state = await self._invoke_agent_sequential(agent_name, state, run_id)
            await self._checkpoint(db, state, run_id, agent_name)
            if (
                agent_name == "complexity-routing"
                and state.status == CaseStatusEnum.escalated
            ):
                logger.warning(
                    "Mesh pipeline halted at complexity-routing: escalated (case_id=%s)",
                    state.case_id,
                )
                return state

        # L2 — parallel fan-out via the aggregator.
        state = await self._invoke_l2_fanout(state, run_id)
        await self._checkpoint(db, state, run_id, "layer2-aggregator")

        # L3 — sequential agents 6–9 with the post-L6 KB hook and the
        # post-L9 output-integrity + fairness halts.
        for agent_name in L3_AGENTS:
            state = await self._invoke_agent_sequential(agent_name, state, run_id)
            await self._checkpoint(db, state, run_id, agent_name)

            if agent_name == "legal-knowledge" and judge_vector_store_id:
                state = await self._apply_judge_kb_hook(state, judge_vector_store_id)

            if agent_name == "governance-verdict":
                maybe_halted = self._apply_governance_halts(state)
                if maybe_halted is not None:
                    return maybe_halted

        logger.info(
            "Mesh pipeline completed for case_id=%s run_id=%s status=%s",
            state.case_id,
            run_id,
            state.status,
        )
        return state

    async def run_from(
        self,
        case_state: CaseState,
        start_agent: str,
        judge_vector_store_id: str | None = None,
        *,
        db: Any = None,
        run_id: str | None = None,
    ) -> CaseState:
        """Re-enter the mesh pipeline at `start_agent` and run downstream.

        Topology-aware dispatch:
            L1 agent      → L1[idx:] sequential + full L2 fanout + L3 sequential
            L2 agent      → full L2 fanout + L3 sequential (aggregator barrier
                            requires all three L2 agents to run as a unit)
            L3 agent      → L3[idx:] sequential

        Skips the input guardrail — the caller is re-running from a
        mid-pipeline state, not raw input. All downstream hooks
        (judge KB, escalation halt, governance halts) still fire.
        """
        state = case_state
        run_id = run_id or uuid.uuid4().hex

        if start_agent in L1_AGENTS:
            l1_start = L1_AGENTS.index(start_agent)
            for agent_name in L1_AGENTS[l1_start:]:
                state = await self._invoke_agent_sequential(agent_name, state, run_id)
                await self._checkpoint(db, state, run_id, agent_name)
                if (
                    agent_name == "complexity-routing"
                    and state.status == CaseStatusEnum.escalated
                ):
                    return state
            state = await self._invoke_l2_fanout(state, run_id)
            await self._checkpoint(db, state, run_id, AGGREGATOR_NAME)
            l3_start = 0
        elif start_agent in L2_AGENTS:
            state = await self._invoke_l2_fanout(state, run_id)
            await self._checkpoint(db, state, run_id, AGGREGATOR_NAME)
            l3_start = 0
        elif start_agent in L3_AGENTS:
            l3_start = L3_AGENTS.index(start_agent)
        else:
            raise ValueError(
                f"Unknown start_agent '{start_agent}'. "
                f"Must be one of {L1_AGENTS + L2_AGENTS + L3_AGENTS}"
            )

        for agent_name in L3_AGENTS[l3_start:]:
            state = await self._invoke_agent_sequential(agent_name, state, run_id)
            await self._checkpoint(db, state, run_id, agent_name)

            if agent_name == "legal-knowledge" and judge_vector_store_id:
                state = await self._apply_judge_kb_hook(state, judge_vector_store_id)

            if agent_name == "governance-verdict":
                maybe_halted = self._apply_governance_halts(state)
                if maybe_halted is not None:
                    return maybe_halted

        return state

    # ------------------------------------------------------------------
    # Inter-agent hooks (ported from PipelineRunner)
    # ------------------------------------------------------------------

    async def _apply_input_guardrail(self, state: CaseState) -> CaseState:
        description = state.case_metadata.get("description", "") if state.case_metadata else ""
        if not description:
            return state
        result = await check_input_injection(description, self._client)
        if not result.get("blocked"):
            return state
        logger.warning(
            "Input injection detected (method=%s, case_id=%s): %s",
            result["method"],
            state.case_id,
            result["reason"],
        )
        state.case_metadata["description"] = result["sanitized_text"]
        return append_audit_entry(
            state,
            agent="guardrails",
            action="input_injection_blocked",
            input_payload={"method": result["method"]},
            output_payload={"reason": result["reason"]},
        )

    async def _apply_judge_kb_hook(
        self,
        state: CaseState,
        judge_vector_store_id: str,
    ) -> CaseState:
        try:
            from src.services.knowledge_base import search_kb

            query = state.case_metadata.get("description", "") if state.case_metadata else ""
            if not query and state.extracted_facts:
                query = str(state.extracted_facts)[:500]
            kb_results = await search_kb(
                judge_vector_store_id, query, max_results=5
            )
            return state.model_copy(update={"judge_kb_results": kb_results})
        except Exception as exc:
            logger.warning("Judge KB search failed: %s", exc)
            return state

    def _apply_governance_halts(self, state: CaseState) -> CaseState | None:
        integrity = validate_output_integrity(state.model_dump())
        if not integrity["passed"]:
            logger.error(
                "Output integrity check FAILED (case_id=%s): %s",
                state.case_id,
                integrity["issues"],
            )
            state = append_audit_entry(
                state,
                agent="guardrails",
                action="output_integrity_failed",
                output_payload=integrity,
            )
            state.status = CaseStatusEnum.escalated
            return state
        if state.fairness_check and state.fairness_check.get("critical_issues_found"):
            logger.warning(
                "Mesh pipeline halted: critical fairness issues (case_id=%s)",
                state.case_id,
            )
            return state.model_copy(update={"status": CaseStatusEnum.escalated})
        return None

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
            await self._a2a.publish(request_topic, envelope, reply_to=reply_to)
            response = await self._a2a.await_response(task_id, timeout=self._agent_timeout)
        except Exception as exc:
            await self._emit_progress(agent_name, state, "failed", error=str(exc)[:500])
            raise

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
        for agent_name in L2_AGENTS:
            agent_key = L2_AGENT_KEY[agent_name]
            sub_task_id = new_task_id(f"{agent_name}-{run_id[:8]}")
            await self._stash_sub_task(sub_task_id, agent_key, case_id, run_id)
            agg_reply_to = _response_topic(
                self._namespace, AGGREGATOR_NAME, sub_task_id
            )
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
            publish_coros.append(
                self._a2a.publish(request_topic, envelope, reply_to=agg_reply_to)
            )
            await self._emit_progress(agent_name, state, "started")

        await asyncio.gather(*publish_coros)

        try:
            merged_response = await self._a2a.await_response(
                mesh_task_id, timeout=L2_BARRIER_TIMEOUT_SECONDS
            )
        except TimeoutError:
            for agent_name in L2_AGENTS:
                await self._emit_progress(
                    agent_name, state, "failed", error="L2 barrier timeout"
                )
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
        to merging the fragment onto `prior_state`.
        """
        payload = parse_send_task_response(envelope)
        if not payload:
            raise RuntimeError(
                f"Empty/unparseable response from agent {agent_name!r}"
            )
        # If payload looks like a full CaseState (has case_id), validate directly.
        if "case_id" in payload:
            return CaseState.model_validate(payload)
        # Otherwise treat as a fragment and merge.
        merged = {**prior_state.model_dump(mode="json"), **payload}
        return CaseState.model_validate(merged)

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

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _checkpoint(
        self,
        db: Any,
        state: CaseState,
        run_id: str,
        agent_name: str,
    ) -> None:
        if db is None:
            return
        await persist_case_state(
            db,
            case_id=state.case_id,
            run_id=run_id,
            agent_name=agent_name,
            state=state,
        )
