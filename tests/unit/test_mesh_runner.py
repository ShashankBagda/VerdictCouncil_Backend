"""Unit tests for MeshPipelineRunner.

Exercises the A2A orchestration surface (topic construction, request
envelope shape, reply-to routing, Redis correlation stashing, L2 fan-out,
escalation halt) using the in-memory `FakeA2AClient` — no Solace broker
required. Integration coverage against a live mesh lives in
`tests/integration/test_mesh_pipeline.py` (Phase 4).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.pipeline._a2a_client import FakeA2AClient, build_send_task_request
from src.pipeline.mesh_runner import (
    AGGREGATOR_NAME,
    L2_AGENT_KEY,
    L2_AGENTS,
    MESH_RUNNER_NAME,
    MeshPipelineRunner,
)
from src.shared.case_state import CaseState, CaseStatusEnum

NAMESPACE = "verdictcouncil"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal async Redis stand-in supporting the methods mesh_runner uses."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value.encode() if isinstance(value, str) else value
        return True

    async def get(self, key):
        return self.store.get(key)


def _case_state(**overrides) -> CaseState:
    base = {
        "case_id": "00000000-0000-0000-0000-000000000001",
        "status": CaseStatusEnum.pending,
        "case_metadata": {"description": "Test case"},
        "raw_documents": [{"doc_id": "d1", "text": "Evidence A"}],
    }
    base.update(overrides)
    return CaseState(**base)


def _send_task_response(task_id: str, case_state_dict: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": task_id,
        "result": {
            "id": task_id,
            "status": {
                "state": "completed",
                "message": {
                    "role": "agent",
                    "parts": [{"type": "data", "data": case_state_dict}],
                },
                "timestamp": datetime.now(UTC).isoformat(),
            },
        },
    }


def _fake_session_factory():
    """Return a callable mimicking an `async_sessionmaker` for unit tests.

    Each call returns an async context manager that yields an `AsyncMock`
    standing in for an `AsyncSession`. `persist_case_state` will happily
    swallow its own errors on the mock, so checkpoints are no-ops in
    unit tests.
    """
    factory = MagicMock(name="session_factory")
    factory.calls = []  # type: ignore[attr-defined]

    @asynccontextmanager
    async def _cm():
        session = AsyncMock(name="AsyncSession")
        factory.calls.append(session)  # type: ignore[attr-defined]
        try:
            yield session
        finally:
            pass

    factory.side_effect = lambda: _cm()
    return factory


def _make_runner(
    a2a_client: FakeA2AClient,
    redis_client: _FakeRedis,
) -> MeshPipelineRunner:
    return MeshPipelineRunner(
        a2a_client=a2a_client,
        session_factory=_fake_session_factory(),
        client=AsyncMock(),  # OpenAI client only used by the input guardrail
        redis_client=redis_client,
        namespace=NAMESPACE,
        agent_timeout_seconds=2.0,
    )


@pytest.fixture(autouse=True)
def _silence_sse(monkeypatch):
    """Swap out Redis-backed progress publisher with a no-op."""
    monkeypatch.setattr("src.pipeline.mesh_runner.publish_progress", AsyncMock(return_value=None))


@pytest.fixture(autouse=True)
def _skip_guardrail(monkeypatch):
    """Default: input guardrail returns non-blocked (no sanitization)."""

    async def _noop(_text, _client):
        return {"blocked": False, "method": "", "reason": "", "sanitized_text": ""}

    monkeypatch.setattr("src.pipeline.hooks.check_input_injection", _noop)


# ---------------------------------------------------------------------------
# SendTaskRequest envelope
# ---------------------------------------------------------------------------


def test_build_send_task_request_shape():
    env = build_send_task_request(
        task_id="t-1",
        session_id="r-1",
        payload={"case_id": "c-1"},
        metadata={"agent_name": "case-processing"},
    )
    assert env["jsonrpc"] == "2.0"
    assert env["id"] == "t-1"
    assert env["method"] == "tasks/send"
    params = env["params"]
    assert params["id"] == "t-1"
    assert params["sessionId"] == "r-1"
    assert params["metadata"]["agent_name"] == "case-processing"
    parts = params["message"]["parts"]
    assert parts == [{"type": "data", "data": {"case_id": "c-1"}}]


# ---------------------------------------------------------------------------
# Single-agent sequential path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l1_agent_invocation_publishes_with_correct_topic_and_reply_to():
    a2a = FakeA2AClient()
    redis = _FakeRedis()
    state = _case_state()

    def resolver(topic, envelope, reply_to):
        return _send_task_response(envelope["id"], state.model_dump(mode="json"))

    a2a.auto_resolver = resolver

    runner = _make_runner(a2a, redis)
    result = await runner._invoke_agent_sequential("case-processing", state, run_id="r1")

    assert result.case_id == state.case_id
    assert len(a2a.publishes) == 1
    topic, envelope, reply_to = a2a.publishes[0]
    assert topic == f"{NAMESPACE}/a2a/v1/agent/request/case-processing"
    assert reply_to.startswith(f"{NAMESPACE}/a2a/v1/agent/response/{MESH_RUNNER_NAME}/")
    assert envelope["params"]["metadata"]["agent_name"] == "case-processing"


@pytest.mark.asyncio
async def test_l1_agent_invocation_propagates_response_failure():
    a2a = FakeA2AClient()
    a2a.auto_resolver = lambda *_: None  # never resolves
    redis = _FakeRedis()
    runner = _make_runner(a2a, redis)
    runner._agent_timeout = 0.05  # force timeout quickly

    with pytest.raises(asyncio.TimeoutError):
        await runner._invoke_agent_sequential("case-processing", _case_state(), run_id="r1")


# ---------------------------------------------------------------------------
# L2 parallel fan-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l2_fanout_publishes_three_parallel_with_aggregator_reply_to():
    a2a = FakeA2AClient()
    redis = _FakeRedis()
    state = _case_state()
    run_id = "run-xyz"

    merged = state.model_dump(mode="json")
    merged["evidence_analysis"] = {"exhibits": []}
    merged["extracted_facts"] = {"timeline": []}
    merged["witnesses"] = {"statements": []}

    publish_count = [0]

    def resolver(topic, envelope, reply_to):
        agent = topic.rsplit("/", 1)[-1]
        if agent in L2_AGENTS:
            publish_count[0] += 1
            if publish_count[0] == 3:
                mesh_task_id = f"layer2-{state.case_id}-{run_id}"
                return _send_task_response(mesh_task_id, merged)
            return None
        return _send_task_response(envelope["id"], state.model_dump(mode="json"))

    a2a.auto_resolver = resolver

    runner = _make_runner(a2a, redis)
    result = await runner._invoke_l2_fanout(state, run_id=run_id)

    # Three L2 publishes
    l2_pubs = [p for p in a2a.publishes if p[0].rsplit("/", 1)[-1] in L2_AGENTS]
    assert len(l2_pubs) == 3
    published_agents = {p[0].rsplit("/", 1)[-1] for p in l2_pubs}
    assert published_agents == set(L2_AGENTS)

    # All three replyTo's point at the aggregator wildcard
    for _topic, _env, reply_to in l2_pubs:
        assert reply_to.startswith(f"{NAMESPACE}/a2a/v1/agent/response/{AGGREGATOR_NAME}/")

    # Redis stashes present: per sub-task correlation + per-run meta
    sub_task_keys = [k for k in redis.store if k.startswith("vc:aggregator:sub_task:")]
    assert len(sub_task_keys) == 3
    for k in sub_task_keys:
        agent_key, case_id, rid = redis.store[k].decode().split("|")
        assert agent_key in L2_AGENT_KEY.values()
        assert case_id == state.case_id
        assert rid == run_id

    meta_key = f"vc:aggregator:run:{state.case_id}:{run_id}:meta"
    assert meta_key in redis.store

    # Merged state returned with L2 fields populated
    assert result.evidence_analysis is not None and result.evidence_analysis.exhibits == []
    assert result.extracted_facts is not None and result.extracted_facts.timeline == []
    assert result.witnesses is not None and result.witnesses.statements == []


@pytest.mark.asyncio
async def test_l2_fanout_raises_on_barrier_timeout():
    a2a = FakeA2AClient()
    redis = _FakeRedis()

    # Resolver publishes but never returns merged response
    a2a.auto_resolver = lambda *_: None

    runner = _make_runner(a2a, redis)
    # Patch the barrier timeout to fail fast
    with (
        patch("src.pipeline.mesh_runner.L2_BARRIER_TIMEOUT_SECONDS", 0.05),
        pytest.raises(asyncio.TimeoutError),
    ):
        await runner._invoke_l2_fanout(_case_state(), run_id="r1")


# ---------------------------------------------------------------------------
# Full pipeline run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_pipeline_runs_all_nine_agents_via_mesh():
    a2a = FakeA2AClient()
    redis = _FakeRedis()
    state = _case_state()

    l2_count = [0]

    def resolver(topic, envelope, reply_to):
        agent = topic.rsplit("/", 1)[-1]
        if agent in L2_AGENTS:
            l2_count[0] += 1
            if l2_count[0] == 3:
                merged = state.model_dump(mode="json")
                merged["evidence_analysis"] = {}
                merged["extracted_facts"] = {}
                merged["witnesses"] = {}
                # Find the mesh task id from the stashed run meta
                # Sub-task correlation gives us run_id
                sub_task_value = next(
                    iter(
                        v.decode()
                        for k, v in redis.store.items()
                        if k.startswith("vc:aggregator:sub_task:")
                    )
                )
                _agent_key, case_id, run_id = sub_task_value.split("|")
                mesh_task_id = f"layer2-{case_id}-{run_id}"
                return _send_task_response(mesh_task_id, merged)
            return None
        return _send_task_response(envelope["id"], state.model_dump(mode="json"))

    a2a.auto_resolver = resolver

    runner = _make_runner(a2a, redis)
    result = await runner.run(state)

    assert result.case_id == state.case_id

    # 2 (L1) + 3 (L2) + 4 (L3) = 9 request publishes
    request_topics = [p[0] for p in a2a.publishes]
    agents_published = [t.rsplit("/", 1)[-1] for t in request_topics]
    expected = [
        "case-processing",
        "complexity-routing",
        *L2_AGENTS,
        "legal-knowledge",
        "argument-construction",
        "hearing-analysis",
        "hearing-governance",
    ]
    # Order matters for L1/L3, not within L2 (parallel)
    assert agents_published[:2] == expected[:2]
    assert set(agents_published[2:5]) == set(L2_AGENTS)
    assert agents_published[5:] == expected[5:]


@pytest.mark.asyncio
async def test_complexity_routing_escalated_status_forced_to_processing():
    """ComplexityEscalationHook no longer halts the pipeline.

    When complexity-routing sets status=escalated, the hook forces it
    back to processing so the pipeline continues past L1 agents. The
    escalation halt was removed in favour of the 4-gate HITL review model.
    We patch the L2 barrier timeout to fail fast (0.05s) so the test
    completes without running the full mesh.
    """
    a2a = FakeA2AClient()
    redis = _FakeRedis()
    state = _case_state()

    def resolver(topic, envelope, reply_to):
        agent = topic.rsplit("/", 1)[-1]
        dumped = state.model_dump(mode="json")
        if agent == "complexity-routing":
            dumped["status"] = CaseStatusEnum.escalated.value
        # L2 fanout agents: never return so L2 barrier times out fast
        if agent in L2_AGENTS:
            return None
        return _send_task_response(envelope["id"], dumped)

    a2a.auto_resolver = resolver

    runner = _make_runner(a2a, redis)
    with (
        patch("src.pipeline.mesh_runner.L2_BARRIER_TIMEOUT_SECONDS", 0.05),
        pytest.raises(asyncio.TimeoutError),
    ):
        await runner.run(state)

    # Both L1 agents ran and L2 was reached — pipeline was NOT halted by the
    # complexity-routing escalation.  If the old halt code were still active,
    # no L2 agent topic would ever be published.
    published_agents = [p[0].rsplit("/", 1)[-1] for p in a2a.publishes]
    assert "case-processing" in published_agents
    assert "complexity-routing" in published_agents
    assert any(a in published_agents for a in L2_AGENTS)


# ---------------------------------------------------------------------------
# Field ownership enforcement on agent responses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_agent_response_strips_unauthorized_fields():
    """A misbehaving agent that writes outside its FIELD_OWNERSHIP set
    must have the unauthorized writes stripped — the runner must not
    persist them to the shared CaseState.
    """
    a2a = FakeA2AClient()
    redis = _FakeRedis()
    runner = _make_runner(a2a, redis)

    prior = _case_state(
        evidence_analysis={"exhibits": ["legit"]},
        arguments={"claim": "original"},
    )

    # witness-analysis owns `witnesses` only; it tries to overwrite
    # `evidence_analysis` (owned by evidence-analysis) and `arguments`
    # (owned by argument-construction).
    rogue_payload = prior.model_dump(mode="json")
    rogue_payload["witnesses"] = {"statements": ["w1"]}
    rogue_payload["evidence_analysis"] = {"exhibits": ["tampered"]}
    rogue_payload["arguments"] = {"claim": "overwritten"}

    envelope = _send_task_response("t-witness", rogue_payload)
    result = runner._parse_agent_response(envelope, prior, "witness-analysis")

    # Authorized write is kept.
    assert result.witnesses is not None and result.witnesses.statements == ["w1"]
    # Unauthorized writes are reverted to prior state.
    assert result.evidence_analysis is not None and result.evidence_analysis.exhibits == ["legit"]
    assert result.arguments == {"claim": "original"}


@pytest.mark.asyncio
async def test_parse_agent_response_accepts_authorized_fragment():
    """When the agent returns only its owned fields, the merged result
    keeps prior state intact and applies the agent's write.
    """
    a2a = FakeA2AClient()
    redis = _FakeRedis()
    runner = _make_runner(a2a, redis)

    prior = _case_state(evidence_analysis={"exhibits": ["keep"]})
    # Fragment response — no case_id — with only witnesses.
    envelope = _send_task_response("t-frag", {"witnesses": {"statements": ["w1"]}})
    result = runner._parse_agent_response(envelope, prior, "witness-analysis")

    assert result.witnesses is not None and result.witnesses.statements == ["w1"]
    assert result.evidence_analysis is not None and result.evidence_analysis.exhibits == ["keep"]


# ---------------------------------------------------------------------------
# Prereq A — agent_response audit entry (B1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_agent_response_emits_agent_response_audit_entry():
    """Mesh runner must emit `action="agent_response"` audit entries so
    `routes/judge.py:367` and `routes/case_data.py:93` continue to see
    per-agent outputs after the runner switch.
    """
    a2a = FakeA2AClient()
    redis = _FakeRedis()
    runner = _make_runner(a2a, redis)

    prior = _case_state()
    fragment = {"witnesses": {"statements": ["w1"]}}
    envelope = _send_task_response("t-audit", fragment)

    result = runner._parse_agent_response(envelope, prior, "witness-analysis")

    audit_entries = [e for e in result.audit_log if e.action == "agent_response"]
    assert len(audit_entries) == 1, (
        "exactly one agent_response entry should be appended per successful parse"
    )
    entry = audit_entries[0]
    assert entry.agent == "witness-analysis"
    assert entry.output_payload == fragment  # raw agent payload, unfiltered


# ---------------------------------------------------------------------------
# Prereq C — run_id invariant (H2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_id_invariant_mismatch_raises():
    """Passing a run_id that differs from state.run_id is a programming
    error — the runner refuses rather than silently picking one.
    """
    a2a = FakeA2AClient()
    redis = _FakeRedis()
    runner = _make_runner(a2a, redis)
    state = _case_state()  # state.run_id is auto-generated

    with pytest.raises(ValueError, match="run_id invariant violated"):
        await runner.run(state, run_id="deliberately-mismatched")


@pytest.mark.asyncio
async def test_run_id_invariant_defaults_to_state_run_id():
    """When no run_id arg is supplied, the runner uses state.run_id and
    never mints a fresh one.
    """
    a2a = FakeA2AClient()
    redis = _FakeRedis()
    state = _case_state()

    def resolver(topic, envelope, reply_to):
        agent = topic.rsplit("/", 1)[-1]
        if agent in L2_AGENTS:
            # Only respond once all three are seen via the aggregator path.
            if sum(1 for t, _e, _r in a2a.publishes if t.rsplit("/", 1)[-1] in L2_AGENTS) == 3:
                merged = state.model_dump(mode="json")
                merged["evidence_analysis"] = {}
                merged["extracted_facts"] = {}
                merged["witnesses"] = {}
                sub_val = next(
                    iter(
                        v.decode()
                        for k, v in redis.store.items()
                        if k.startswith("vc:aggregator:sub_task:")
                    )
                )
                _k, case_id, rid = sub_val.split("|")
                return _send_task_response(f"layer2-{case_id}-{rid}", merged)
            return None
        return _send_task_response(envelope["id"], state.model_dump(mode="json"))

    a2a.auto_resolver = resolver
    runner = _make_runner(a2a, redis)

    result = await runner.run(state)  # no run_id kwarg

    assert result.run_id == state.run_id


# ---------------------------------------------------------------------------
# Prereq B — checkpoint opens its own short-lived session (H1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkpoint_opens_short_lived_session_per_call():
    """`_checkpoint` must not take an external session — it opens its
    own via `session_factory` so the pool is never held across an A2A
    await. Verified by counting factory invocations across agents.
    """
    a2a = FakeA2AClient()
    redis = _FakeRedis()
    session_factory = _fake_session_factory()
    state = _case_state()

    def resolver(topic, envelope, reply_to):
        agent = topic.rsplit("/", 1)[-1]
        if agent in L2_AGENTS:
            if sum(1 for t, _e, _r in a2a.publishes if t.rsplit("/", 1)[-1] in L2_AGENTS) == 3:
                merged = state.model_dump(mode="json")
                merged["evidence_analysis"] = {}
                merged["extracted_facts"] = {}
                merged["witnesses"] = {}
                sub_val = next(
                    iter(
                        v.decode()
                        for k, v in redis.store.items()
                        if k.startswith("vc:aggregator:sub_task:")
                    )
                )
                _k, case_id, rid = sub_val.split("|")
                return _send_task_response(f"layer2-{case_id}-{rid}", merged)
            return None
        return _send_task_response(envelope["id"], state.model_dump(mode="json"))

    a2a.auto_resolver = resolver

    runner = MeshPipelineRunner(
        a2a_client=a2a,
        session_factory=session_factory,
        client=AsyncMock(),
        redis_client=redis,
        namespace=NAMESPACE,
        agent_timeout_seconds=2.0,
    )

    await runner.run(state)

    # One checkpoint per L1 agent (2) + L2 aggregator (1) + L3 agents (4) = 7.
    assert session_factory.call_count == 7, (
        f"expected 7 short-lived sessions, got {session_factory.call_count}"
    )


# ---------------------------------------------------------------------------
# Phase 3.2 — terminal SSE events on every halt path
# ---------------------------------------------------------------------------


def _terminal_events(mock_publish: AsyncMock) -> list:
    """Pick out the run-level terminal events from all publish calls."""
    return [
        call.args[0]
        for call in mock_publish.await_args_list
        if call.args and call.args[0].agent == "pipeline" and call.args[0].phase == "terminal"
    ]


@pytest.mark.asyncio
async def test_complexity_escalation_no_longer_halts_mesh_pipeline(monkeypatch):
    """ComplexityEscalationHook now forces escalated → processing and does NOT
    halt — the pipeline continues past L1 into L2. The old 'complexity_escalation'
    terminal event is no longer emitted.  We patch the L2 barrier timeout to
    bail out fast so the test doesn't block on the incomplete L2 resolver.
    """
    publish_mock = AsyncMock(return_value=None)
    monkeypatch.setattr("src.pipeline.mesh_runner.publish_progress", publish_mock)

    a2a = FakeA2AClient()
    redis = _FakeRedis()
    state = _case_state()

    def resolver(topic, envelope, reply_to):
        agent = topic.rsplit("/", 1)[-1]
        dumped = state.model_dump(mode="json")
        if agent == "complexity-routing":
            dumped["status"] = CaseStatusEnum.escalated.value
        if agent in L2_AGENTS:
            return None  # L2 never resolves → barrier times out
        return _send_task_response(envelope["id"], dumped)

    a2a.auto_resolver = resolver
    runner = _make_runner(a2a, redis)
    with (
        patch("src.pipeline.mesh_runner.L2_BARRIER_TIMEOUT_SECONDS", 0.05),
        pytest.raises(asyncio.TimeoutError),
    ):
        await runner.run(state)

    # Hook forces escalated → processing and does NOT halt; both L1 agents ran.
    # Pipeline reached L2 (confirmed by L2 agent publishes), not halted at L1.
    published_agents = [p[0].rsplit("/", 1)[-1] for p in a2a.publishes]
    assert "complexity-routing" in published_agents
    assert any(a in published_agents for a in L2_AGENTS)

    # The old 'complexity_escalation' terminal reason is gone.
    terminals = _terminal_events(publish_mock)
    assert not any(t.detail.get("reason") == "complexity_escalation" for t in terminals)


@pytest.mark.asyncio
async def test_governance_critical_issues_no_longer_halt_mesh_pipeline(monkeypatch):
    """GovernanceHaltHook now just logs fairness issues; it does NOT halt the
    pipeline or set status=escalated. Pipeline completes normally even when
    critical_issues_found is True — the judge sees the flags at gate 4.
    """
    publish_mock = AsyncMock(return_value=None)
    monkeypatch.setattr("src.pipeline.mesh_runner.publish_progress", publish_mock)

    a2a = FakeA2AClient()
    redis = _FakeRedis()
    state = _case_state()

    l2_count = [0]

    def resolver(topic, envelope, reply_to):
        agent = topic.rsplit("/", 1)[-1]
        if agent in L2_AGENTS:
            l2_count[0] += 1
            if l2_count[0] == 3:
                merged = state.model_dump(mode="json")
                merged["evidence_analysis"] = {}
                merged["extracted_facts"] = {}
                merged["witnesses"] = {}
                sub_val = next(
                    iter(
                        v.decode()
                        for k, v in redis.store.items()
                        if k.startswith("vc:aggregator:sub_task:")
                    )
                )
                _k, case_id, rid = sub_val.split("|")
                return _send_task_response(f"layer2-{case_id}-{rid}", merged)
            return None
        dumped = state.model_dump(mode="json")
        if agent == "hearing-governance":
            dumped["fairness_check"] = {
                "critical_issues_found": True,
                "audit_passed": False,
                "issues": ["x"],
                "recommendations": [],
            }
        return _send_task_response(envelope["id"], dumped)

    a2a.auto_resolver = resolver
    runner = _make_runner(a2a, redis)
    result = await runner.run(state)

    # Pipeline completes; fairness flags are captured but do not halt.
    assert result.fairness_check is not None
    assert result.fairness_check.critical_issues_found is True
    assert result.status != CaseStatusEnum.escalated

    # No 'governance_halt' terminal event — the old halt is gone.
    terminals = _terminal_events(publish_mock)
    assert not any(t.detail.get("reason") == "governance_halt" for t in terminals)


@pytest.mark.asyncio
async def test_terminal_event_emitted_on_agent_timeout(monkeypatch):
    """A sequential agent timeout inside run() emits a terminal event with
    reason='agent_timeout' (distinct from 'l2_barrier_timeout' so analytics
    can attribute the halt correctly) before the exception propagates.
    """
    publish_mock = AsyncMock(return_value=None)
    monkeypatch.setattr("src.pipeline.mesh_runner.publish_progress", publish_mock)

    a2a = FakeA2AClient()
    a2a.auto_resolver = lambda *_: None  # never resolves → TimeoutError
    redis = _FakeRedis()
    runner = _make_runner(a2a, redis)
    runner._agent_timeout = 0.05

    with pytest.raises(asyncio.TimeoutError):
        await runner.run(_case_state())

    terminals = _terminal_events(publish_mock)
    assert len(terminals) == 1
    assert terminals[0].detail["reason"] == "agent_timeout"
    assert terminals[0].detail["stopped_at"] == "case-processing"


@pytest.mark.asyncio
async def test_terminal_event_emitted_on_orchestrator_exception(monkeypatch):
    """A non-timeout exception inside run() falls into the generic handler
    and emits reason='exception' with the current agent as stopped_at.
    """
    publish_mock = AsyncMock(return_value=None)
    monkeypatch.setattr("src.pipeline.mesh_runner.publish_progress", publish_mock)

    a2a = FakeA2AClient()

    def resolver(topic, envelope, reply_to):
        raise RuntimeError("boom")

    a2a.auto_resolver = resolver
    redis = _FakeRedis()
    runner = _make_runner(a2a, redis)

    with pytest.raises(RuntimeError, match="boom"):
        await runner.run(_case_state())

    terminals = _terminal_events(publish_mock)
    assert len(terminals) == 1
    assert terminals[0].detail["reason"] == "exception"
    assert terminals[0].detail["stopped_at"] == "case-processing"


@pytest.mark.asyncio
async def test_terminal_event_emitted_on_l2_barrier_timeout(monkeypatch):
    """L2 barrier timeout inside the full run() emits exactly one terminal
    event at the orchestrator layer with reason='l2_barrier_timeout'. This
    drives the full run() path rather than `_invoke_l2_fanout` directly so
    we also verify the outer TimeoutError handler doesn't double-emit.
    """
    publish_mock = AsyncMock(return_value=None)
    monkeypatch.setattr("src.pipeline.mesh_runner.publish_progress", publish_mock)

    a2a = FakeA2AClient()
    redis = _FakeRedis()
    state = _case_state()

    def resolver(topic, envelope, reply_to):
        agent = topic.rsplit("/", 1)[-1]
        if agent in L2_AGENTS:
            # Never resolve the aggregator merge → barrier TimeoutError
            return None
        return _send_task_response(envelope["id"], state.model_dump(mode="json"))

    a2a.auto_resolver = resolver
    runner = _make_runner(a2a, redis)

    with (
        patch("src.pipeline.mesh_runner.L2_BARRIER_TIMEOUT_SECONDS", 0.05),
        pytest.raises(asyncio.TimeoutError),
    ):
        await runner.run(state)

    terminals = _terminal_events(publish_mock)
    assert len(terminals) == 1, (
        f"expected exactly one terminal event, got {len(terminals)}: "
        f"{[t.detail for t in terminals]}"
    )
    assert terminals[0].detail == {
        "reason": "l2_barrier_timeout",
        "stopped_at": "layer2-aggregator",
    }


@pytest.mark.asyncio
async def test_run_from_complexity_escalation_hook_forces_processing(monkeypatch):
    """What-If re-entry at complexity-routing: hook forces escalated → processing
    and the pipeline continues into L2. The old 'complexity_escalation' terminal
    is no longer emitted. L2 barrier times out fast so the test stays quick.
    """
    publish_mock = AsyncMock(return_value=None)
    monkeypatch.setattr("src.pipeline.mesh_runner.publish_progress", publish_mock)

    a2a = FakeA2AClient()
    redis = _FakeRedis()
    state = _case_state()

    def resolver(topic, envelope, reply_to):
        agent = topic.rsplit("/", 1)[-1]
        dumped = state.model_dump(mode="json")
        if agent == "complexity-routing":
            dumped["status"] = CaseStatusEnum.escalated.value
        if agent in L2_AGENTS:
            return None  # L2 never resolves → barrier times out
        return _send_task_response(envelope["id"], dumped)

    a2a.auto_resolver = resolver
    runner = _make_runner(a2a, redis)
    with (
        patch("src.pipeline.mesh_runner.L2_BARRIER_TIMEOUT_SECONDS", 0.05),
        pytest.raises(asyncio.TimeoutError),
    ):
        await runner.run_from(state, start_agent="complexity-routing")

    # Hook forced processing and pipeline continued past complexity-routing into L2.
    published_agents = [p[0].rsplit("/", 1)[-1] for p in a2a.publishes]
    assert "complexity-routing" in published_agents
    assert any(a in published_agents for a in L2_AGENTS)

    terminals = _terminal_events(publish_mock)
    assert not any(t.detail.get("reason") == "complexity_escalation" for t in terminals)
