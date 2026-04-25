"""Sprint 4 4.A5.4 — What-If R-10 cross-judge isolation contract.

Three-part acceptance:

(a) Fork preserves the original case state — running the fork must not
    mutate the original thread's terminal CaseState.
(b) Modifications applied to the fork only — running both threads
    forward produces visibly different terminal states.
(c) Cross-judge isolation — judge A creates a what-if scenario; judge B
    fetching that scenario via the API is rejected (404). Saver-level
    thread_id scoping is the structural hint; the API ``created_by``
    check is the authoritative gate.

(a) and (b) live in ``test_whatif_fork.py`` against the compiled graph;
this file focuses on (c) — the API-layer rejection — plus a saver-level
companion that locks the parent-trace lineage stamp at the boundary
between the two layers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.deps import get_current_user, get_db
from src.models.case import Case, CaseStatus
from src.models.user import User, UserRole
from src.models.what_if import (
    ModificationType,
    ScenarioStatus,
    WhatIfScenario,
)


# ---------------------------------------------------------------------------
# FastAPI fixtures (mirror tests/unit/test_cases.py shape)
# ---------------------------------------------------------------------------


def _make_judge(judge_id: uuid.UUID | None = None, name: str = "Judge Alpha") -> MagicMock:
    user = MagicMock(spec=User)
    user.id = judge_id or uuid.uuid4()
    user.name = name
    user.email = f"{name.lower().replace(' ', '.')}@example.com"
    user.role = UserRole.judge
    user.password_hash = "hashed"
    user.created_at = datetime.now(UTC)
    user.updated_at = None
    return user


def _make_case(case_id: uuid.UUID, owner_id: uuid.UUID) -> MagicMock:
    case = MagicMock(spec=Case)
    case.id = case_id
    case.created_by = owner_id
    case.status = CaseStatus.ready_for_review
    case.latest_run_id = "orig-run"
    return case


def _make_scenario(
    scenario_id: uuid.UUID,
    case_id: uuid.UUID,
    created_by: uuid.UUID,
) -> MagicMock:
    scenario = MagicMock(spec=WhatIfScenario)
    scenario.id = scenario_id
    scenario.case_id = case_id
    scenario.created_by = created_by
    scenario.original_run_id = "orig-run"
    scenario.scenario_run_id = f"fork-run-{scenario_id}"
    scenario.modification_type = ModificationType.evidence_exclusion
    scenario.modification_description = "exclude e1"
    scenario.modification_payload = {"evidence_id": "e1"}
    scenario.status = ScenarioStatus.completed
    scenario.created_at = datetime.now(UTC)
    scenario.completed_at = datetime.now(UTC)
    scenario.result = None  # no diff yet — keeps the response slim
    return scenario


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.fixture
def judge_alpha() -> MagicMock:
    return _make_judge(name="Judge Alpha")


@pytest.fixture
def judge_beta() -> MagicMock:
    return _make_judge(name="Judge Beta")


@pytest.fixture
def app_factory():
    """Build a FastAPI test app overriding auth + db deps for a given user."""

    def _factory(*, current_user: MagicMock, db_lookups: list):
        app = create_app()
        db = AsyncMock()
        # FastAPI deps consume db lookups in the order the route does;
        # each entry is the value `scalar_one_or_none()` should return.
        db.execute = AsyncMock(side_effect=[_scalar_result(v) for v in db_lookups])
        app.dependency_overrides[get_current_user] = lambda: current_user
        app.dependency_overrides[get_db] = lambda: db
        return app, db

    return _factory


# ---------------------------------------------------------------------------
# (c) Cross-judge isolation — the API gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_b_cannot_read_judge_a_whatif_scenario(
    app_factory, judge_alpha, judge_beta
) -> None:
    """Judge B fetching judge A's scenario via GET must return 404.

    The route filters by ``(scenario_id, case_id)`` today; without a
    ``created_by == current_user.id`` clause, judge B can read judge
    A's hypothetical even though the saver's thread_id is judge-A
    scoped. The API check is the authoritative gate — fix it here.
    """
    case_id = uuid.uuid4()
    scenario_id = uuid.uuid4()

    # Judge A owns both the case and the scenario.
    case = _make_case(case_id, owner_id=judge_alpha.id)
    scenario = _make_scenario(scenario_id, case_id, created_by=judge_alpha.id)

    # Judge B authenticates and tries to read it. The route does:
    #   1. scenario lookup (we return judge A's scenario)
    # If the route had no created_by check this would 200; with the
    # check, the lookup must fail at the auth layer and return 404.
    app, _db = app_factory(current_user=judge_beta, db_lookups=[scenario])

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/api/v1/cases/{case_id}/what-if/{scenario_id}"
        )

    assert resp.status_code == 404, (
        f"Judge B reading Judge A's scenario must 404; got {resp.status_code} "
        f"with body {resp.text!r}"
    )


@pytest.mark.asyncio
async def test_judge_a_can_read_own_whatif_scenario(
    app_factory, judge_alpha
) -> None:
    """Judge A fetching their own scenario succeeds — the auth check
    is precise, not a blanket lockdown."""
    case_id = uuid.uuid4()
    scenario_id = uuid.uuid4()
    scenario = _make_scenario(scenario_id, case_id, created_by=judge_alpha.id)

    app, _db = app_factory(current_user=judge_alpha, db_lookups=[scenario])

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/v1/cases/{case_id}/what-if/{scenario_id}")

    assert resp.status_code == 200, (
        f"Judge A reading own scenario must 200; got {resp.status_code} {resp.text!r}"
    )
    body = resp.json()
    assert uuid.UUID(body["id"]) == scenario_id


# ---------------------------------------------------------------------------
# Lineage stamp — saver-level companion to the API check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fork_metadata_links_back_to_parent_run(monkeypatch) -> None:
    """Fork's checkpoint stamp parent_run_id + parent_thread_id so a
    LangSmith trace can be navigated back to the original run.

    This is the saver-side complement to the API gate: the gate stops
    judge B from *reading* the fork; the lineage stamp lets a single
    judge (or an auditor) trace the fork back to its parent.
    """
    from langgraph.checkpoint.memory import InMemorySaver

    # Stub the phase/research factories so build_graph compiles without
    # OpenAI calls.
    from tests.integration.test_whatif_fork import (
        _drive_original_to_terminal,
        _patch_factories,
    )
    from src.services.whatif.fork import WhatIfModification, create_whatif_fork

    _patch_factories(monkeypatch)
    from src.pipeline.graph.builder import build_graph

    compiled = build_graph(checkpointer=InMemorySaver())
    case_id = "abcd0000-0000-0000-0000-000000000001"
    await _drive_original_to_terminal(compiled, case_id)

    fork_tid = await create_whatif_fork(
        graph=compiled,
        case_id=case_id,
        judge_id="judge-alpha",
        modifications=[
            WhatIfModification(
                modification_type="evidence_exclusion", payload={"evidence_id": "e1"}
            )
        ],
    )

    snap = await compiled.aget_state({"configurable": {"thread_id": fork_tid}})
    md = snap.metadata or {}
    parent_thread = md.get("parent_thread_id") or snap.values.get("parent_thread_id")
    assert parent_thread == case_id, (
        f"fork metadata must point to parent thread {case_id!r}; got {parent_thread!r}"
    )
