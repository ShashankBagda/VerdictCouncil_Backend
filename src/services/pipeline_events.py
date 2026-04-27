"""Redis pub/sub fan-out for pipeline progress events (US-002).

Reuses the Redis singleton from ``src.tools.search_precedents`` to avoid
opening a second connection pool. Subscribers get an async generator
that yields events until the case reaches a terminal status (the
``hearing-governance`` agent completing or failing closes the stream).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

from src.api.schemas.pipeline_events import PipelineProgressEvent
from src.tools.search_precedents import _get_redis_client

logger = logging.getLogger(__name__)


def _json_safe(value):
    """Coerce a payload tree into something `json.dumps` can swallow.

    Agent payloads occasionally carry `datetime.date`, `datetime.datetime`,
    `UUID`, Pydantic models, and other non-JSON-native types — bare
    `json.dumps` raises `TypeError: Object of type date is not JSON
    serializable` and the tee-write loses the event. Walking the tree
    once with a permissive coercion is cheaper and safer than letting
    SQLAlchemy's JSONB serializer blow up at commit time.
    """
    from datetime import date, datetime as _dt, time as _time
    from uuid import UUID

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (date, _dt, _time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump(mode="json"))
        except Exception:
            return str(value)
    if hasattr(value, "value") and hasattr(value, "name"):  # enum
        return value.value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(v) for v in value]
    return str(value)


async def _tee_write(case_id: str | object, payload: dict) -> None:
    """Fire-and-forget INSERT into pipeline_events; never raises."""
    try:
        from src.models.pipeline_event import PipelineEvent
        from src.services.database import async_session

        raw_ts = payload.get("ts")
        if isinstance(raw_ts, str):
            ts = datetime.fromisoformat(raw_ts)
        elif isinstance(raw_ts, datetime):
            ts = raw_ts
        else:
            ts = datetime.now(UTC)

        safe_payload = _json_safe(payload)

        async with async_session() as db:
            db.add(
                PipelineEvent(
                    id=uuid.uuid4(),
                    case_id=uuid.UUID(str(case_id)),
                    kind=str(payload.get("kind", "unknown")),
                    schema_version=int(payload.get("schema_version", 1)),
                    agent=payload.get("agent"),
                    ts=ts,
                    payload=safe_payload,
                )
            )
            await db.commit()
    except Exception:
        logger.exception("pipeline_events tee-write failed for case %s", case_id)


_GOVERNANCE_TERMINAL_PHASES = {"completed", "failed"}


def _channel(case_id: str | object) -> str:
    return f"vc:case:{case_id}:progress"


def _is_terminal_event(parsed: dict) -> bool:
    """Close the stream on either the happy path or any halt path.

    - ``hearing-governance`` + ``completed``/``failed`` is the happy-path
      close signal left over from US-002.
    - ``pipeline`` + ``terminal`` is the run-level halt signal the mesh
      runner emits on escalation, barrier timeout, governance halt, etc.
    - ``pipeline`` + ``awaiting_review`` is the gate-pause signal emitted
      after each gate completes. The SSE client closes and reconnects when
      the judge advances to the next gate.
    - ``pipeline`` + ``cancelled`` is emitted when the judge explicitly
      cancels a running pipeline via POST /cases/{id}/cancel.
    """
    # Interrupt frame (Sprint 4 4.A3.7) closes the SSE cycle so the client
    # can mount the gate review panel and reconnect after the judge resumes.
    if parsed.get("kind") == "interrupt":
        return True
    agent = parsed.get("agent")
    phase = parsed.get("phase")
    if agent == "hearing-governance" and phase in _GOVERNANCE_TERMINAL_PHASES:
        return True
    return agent == "pipeline" and phase in {"terminal", "awaiting_review", "cancelled"}


async def publish_progress(event: PipelineProgressEvent) -> None:
    """Fire-and-forget publish of a pipeline progress event.

    Failures are logged but never raised — pipeline execution must not
    be blocked by the observability sidecar.

    Sprint 2 2.C1.5: stamps the active OTEL trace_id onto the event when
    the caller did not supply one. Workers run inside an OTEL context
    re-established from `pipeline_jobs.traceparent`, so this captures the
    original API request's trace.
    """
    if event.trace_id is None:
        from src.api.trace_propagation import current_trace_id

        tid = current_trace_id()
        if tid:
            event = event.model_copy(update={"trace_id": tid})
    try:
        r = await _get_redis_client()
        await r.publish(_channel(event.case_id), event.model_dump_json())
        asyncio.create_task(_tee_write(event.case_id, event.model_dump(mode="json")))
    except Exception:
        logger.exception("Failed to publish pipeline progress event")


async def publish_interrupt(
    case_id: str | object,
    gate: str,
    payload: dict,
) -> None:
    """Publish an InterruptEvent and write legacy `awaiting_review_gateN` compat.

    Sprint 4 4.A3.7. Fired when the LangGraph pipeline pauses at a gate.
    The graph nodes themselves stay side-effect-free (4.A3.2 invariant);
    this function is the boundary layer that materialises the interrupt
    for downstream readers:

    1. Redis pub/sub fan-out to the SSE stream — `<GateReviewPanel>`
       mounts on receipt.
    2. ``pipeline_events`` table tee-write for replay / case-data
       reconstruction.
    3. **Legacy compat:** UPSERT ``cases.status = awaiting_review_gateN``
       and ``cases.gate_state`` JSONB so existing case-list filters and
       watchdog queries (which key off these fields, not the saver) keep
       working through the cutover.

    The DB write is naturally idempotent — the same UPDATE re-fires on
    every replay with identical values. ``publish_interrupt`` itself
    can be called repeatedly for the same (case_id, gate) without
    corruption; consumers dedupe at the (case_id, gate) layer.
    """
    from uuid import UUID as _UUID

    from src.api.schemas.pipeline_events import InterruptEvent
    from src.api.trace_propagation import current_trace_id

    case_uuid = case_id if isinstance(case_id, _UUID) else _UUID(str(case_id))
    trace_id = payload.get("trace_id") or current_trace_id()

    event = InterruptEvent(
        case_id=case_uuid,
        gate=gate,  # type: ignore[arg-type]
        actions=list(payload.get("actions") or []),
        phase_output=payload.get("phase_output"),
        audit_summary=payload.get("audit_summary"),
        trace_id=trace_id,
        ts=datetime.now(UTC),
    )

    # 1 + 2: Redis fan-out + pipeline_events tee-write
    try:
        r = await _get_redis_client()
        await r.publish(_channel(case_id), event.model_dump_json())
        asyncio.create_task(_tee_write(case_id, event.model_dump(mode="json")))
    except Exception:
        logger.exception("Failed to publish InterruptEvent for case %s", case_id)

    # 3: Legacy compat UPSERT — case.status + case.gate_state
    try:
        await _upsert_legacy_gate_status(case_uuid, gate)
    except Exception:
        logger.exception(
            "Failed to upsert legacy gate status for case=%s gate=%s",
            case_id,
            gate,
        )


async def _upsert_legacy_gate_status(case_id: object, gate: str) -> None:
    """UPSERT cases.status + cases.gate_state for the legacy review surface.

    Idempotent — replay-safe by construction (UPDATE … WHERE id = X).
    Skips silently if the case row is missing.
    """
    from src.models.case import Case, CaseStatus
    from src.services.database import async_session

    if gate not in {"gate1", "gate2", "gate3", "gate4"}:
        logger.warning("_upsert_legacy_gate_status: unknown gate %r", gate)
        return

    status_value = f"awaiting_review_{gate}"
    try:
        new_status = CaseStatus(status_value)
    except ValueError:
        logger.warning("_upsert_legacy_gate_status: status %r missing", status_value)
        return

    gate_num = int(gate[-1])
    gate_state = {
        "current_gate": gate_num,
        "awaiting_review": True,
        "rerun_agent": None,
    }

    async with async_session() as db:
        case = await db.get(Case, case_id)
        if case is None:
            logger.info("_upsert_legacy_gate_status: case %s not found", case_id)
            return
        case.status = new_status
        case.gate_state = gate_state
        await db.commit()


async def publish_agent_event(case_id: str | object, event: dict) -> None:
    """Publish a fine-grained agent event (thinking / tool_call / tool_result / llm_response).

    This is the free-form counterpart to `publish_progress`. The
    sequential runner emits these from inside its LLM+tool loop so the
    `/case/<id>/building` UI can show what's actually happening beyond
    agent lifecycle transitions.

    `kind` and `schema_version` are injected here so callers don't need
    to include them; the published payload conforms to AgentEvent.

    Failures are logged but never raised: telemetry must never break a
    running pipeline.
    """
    try:
        from src.api.trace_propagation import current_trace_id

        r = await _get_redis_client()
        stamped = {"kind": "agent", "schema_version": 1, **event}
        if "trace_id" not in stamped:
            stamped["trace_id"] = current_trace_id()
        await r.publish(_channel(case_id), json.dumps(stamped, default=str))
        asyncio.create_task(_tee_write(case_id, stamped))
    except Exception:
        logger.exception("Failed to publish agent event")


async def publish_narration(
    case_id: str | object,
    agent: str,
    content: str,
    chunk_index: int = 0,
) -> None:
    """Publish a natural-language narration chunk from a running agent.

    Narration is the prose counterpart to the structured JSON state commit.
    It is emitted *before* the llm_response event so the UI receives readable
    text as soon as the agent finishes its analysis.
    """
    try:
        from src.api.trace_propagation import current_trace_id

        r = await _get_redis_client()
        event = {
            "kind": "narration",
            "schema_version": 1,
            "case_id": str(case_id),
            "agent": agent,
            "content": content,
            "chunk_index": chunk_index,
            "ts": datetime.now(UTC).isoformat(),
            "trace_id": current_trace_id(),
        }
        await r.publish(_channel(case_id), json.dumps(event, default=str))
        asyncio.create_task(_tee_write(case_id, event))
    except Exception:
        logger.exception("Failed to publish narration event for agent '%s'", agent)


_CANCEL_KEY_TTL = 86400  # 24 hours


def _cancel_key(case_id: str | object) -> str:
    return f"vc:case:{case_id}:cancel_requested"


async def set_cancel_flag(case_id: str | object) -> None:
    """Signal that the pipeline for this case should stop at the next inter-turn check."""
    try:
        r = await _get_redis_client()
        await r.set(_cancel_key(case_id), "1", ex=_CANCEL_KEY_TTL)
    except Exception:
        logger.exception("Failed to set cancel flag for case %s", case_id)


async def check_cancel_flag(case_id: str | object) -> bool:
    """Return True if cancellation has been requested for this case's pipeline."""
    try:
        r = await _get_redis_client()
        return bool(await r.exists(_cancel_key(case_id)))
    except Exception:
        logger.exception("Failed to check cancel flag for case %s", case_id)
        return False


async def clear_cancel_flag(case_id: str | object) -> None:
    """Remove the cancellation flag after the pipeline has handled it."""
    try:
        r = await _get_redis_client()
        await r.delete(_cancel_key(case_id))
    except Exception:
        logger.exception("Failed to clear cancel flag for case %s", case_id)


async def subscribe(case_id: str | object) -> AsyncGenerator[str, None]:
    """Yield JSON-serialized events for a case until hearing-governance closes."""
    r = await _get_redis_client()
    pubsub = r.pubsub()
    try:
        await pubsub.subscribe(_channel(case_id))
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            payload = message["data"]
            yield payload
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if _is_terminal_event(parsed):
                return
    finally:
        await pubsub.unsubscribe(_channel(case_id))
        await pubsub.aclose()
