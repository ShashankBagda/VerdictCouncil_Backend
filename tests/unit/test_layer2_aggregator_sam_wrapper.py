"""Unit tests for the SAM wrapper around Layer2Aggregator.

These exercise the config-wiring and payload-coercion logic in
`src/services/layer2_aggregator/app.py` and `component.py` without
standing up a real Solace AI Connector flow. The underlying
`Layer2Aggregator` Redis logic is already covered by
`tests/unit/test_layer2_aggregator.py`.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.services.layer2_aggregator.app import Layer2AggregatorApp
from src.services.layer2_aggregator.component import Layer2AggregatorComponent


# ---------------------------------------------------------------------------
# Layer2AggregatorApp — config validation + component wiring
# ---------------------------------------------------------------------------


def _app_info(**app_config_overrides):
    base_app_config = {
        "namespace": "verdictcouncil",
        "service_name": "layer2-aggregator",
        "subscriptions": [
            {
                "topic": "verdictcouncil/aggregator/input/evidence-analysis",
                "agent_key": "evidence_analysis",
            },
            {
                "topic": "verdictcouncil/aggregator/input/fact-reconstruction",
                "agent_key": "extracted_facts",
            },
            {
                "topic": "verdictcouncil/aggregator/input/witness-analysis",
                "agent_key": "witnesses",
            },
        ],
        "output_topic": "verdictcouncil/a2a/v1/agent/request/legal-knowledge",
        "redis_url": "redis://localhost:6379/1",
    }
    base_app_config.update(app_config_overrides)
    return {"name": "layer2-aggregator", "broker": {}, "app_config": base_app_config}


def _build_app(app_info):
    """Instantiate Layer2AggregatorApp without booting the real SAI flow machinery."""
    with patch(
        "src.services.layer2_aggregator.app.App.__init__", return_value=None
    ) as mock_base_init:
        app = Layer2AggregatorApp(app_info=app_info)
    return app, mock_base_init, app_info


def test_app_builds_topic_map_and_component_definition():
    app_info = _app_info()
    _, _, resolved = _build_app(app_info)

    components = resolved["components"]
    assert len(components) == 1
    component_def = components[0]

    assert component_def["component_class"] is Layer2AggregatorComponent
    assert component_def["component_name"] == "layer2-aggregator_component"

    topic_map = component_def["component_config"]["topic_map"]
    assert topic_map == {
        "verdictcouncil/aggregator/input/evidence-analysis": "evidence_analysis",
        "verdictcouncil/aggregator/input/fact-reconstruction": "extracted_facts",
        "verdictcouncil/aggregator/input/witness-analysis": "witnesses",
    }

    # Broker subscriptions mirror the input topics (no agent_key leakage)
    broker_subs = component_def["subscriptions"]
    assert [s["topic"] for s in broker_subs] == list(topic_map.keys())
    assert all(set(s.keys()) == {"topic"} for s in broker_subs)

    # Output + redis propagate unchanged
    assert (
        component_def["component_config"]["output_topic"]
        == "verdictcouncil/a2a/v1/agent/request/legal-knowledge"
    )
    assert component_def["component_config"]["redis_url"] == "redis://localhost:6379/1"


def test_app_sets_broker_input_output_and_queue_name():
    app_info = _app_info()
    _, _, resolved = _build_app(app_info)

    broker = resolved["broker"]
    assert broker["input_enabled"] is True
    assert broker["output_enabled"] is True
    assert broker["queue_name"] == "verdictcouncil/q/services/layer2-aggregator"
    assert broker["temporary_queue"] is True


@pytest.mark.parametrize(
    "missing_field",
    ["namespace", "subscriptions", "output_topic", "redis_url"],
)
def test_app_rejects_missing_required_field(missing_field):
    # Start with valid config, then erase the target field
    app_info = _app_info()
    app_info["app_config"][missing_field] = (
        [] if missing_field == "subscriptions" else ""
    )

    with pytest.raises(ValueError, match=missing_field):
        _build_app(app_info)


def test_app_rejects_subscription_missing_agent_key():
    app_info = _app_info(
        subscriptions=[
            {"topic": "verdictcouncil/aggregator/input/evidence-analysis"},
        ]
    )
    with pytest.raises(ValueError, match="agent_key"):
        _build_app(app_info)


# ---------------------------------------------------------------------------
# Layer2AggregatorComponent — topic extraction + payload coercion + invoke
# ---------------------------------------------------------------------------


def _build_component():
    """Build a Layer2AggregatorComponent with only the attributes invoke() touches.

    Bypasses `__init__` entirely so we don't stand up the SAI flow machinery
    or the background asyncio loop.
    """
    component = object.__new__(Layer2AggregatorComponent)
    component.component_config = {
        "topic_map": {
            "verdictcouncil/aggregator/input/evidence-analysis": "evidence_analysis",
            "verdictcouncil/aggregator/input/fact-reconstruction": "extracted_facts",
            "verdictcouncil/aggregator/input/witness-analysis": "witnesses",
        },
        "output_topic": "verdictcouncil/a2a/v1/agent/request/legal-knowledge",
        "redis_url": "redis://localhost:6379/1",
    }
    component._topic_map = component.component_config["topic_map"]
    component._output_topic = component.component_config["output_topic"]
    component._loop = MagicMock()  # run_coroutine_threadsafe is always patched in callers
    component._aggregator = MagicMock()
    return component


def test_coerce_payload_handles_dict_bytes_string():
    coerce = Layer2AggregatorComponent._coerce_payload

    payload = {"case_id": "c-1", "run_id": "r-1", "output": {}, "base_state": {}}
    assert coerce(payload) is payload

    raw = json.dumps(payload).encode("utf-8")
    assert coerce(raw) == payload
    assert coerce(raw.decode()) == payload

    assert coerce(None) == {}
    assert coerce(42) == {}


def test_extract_topic_supports_get_topic_and_attribute():
    extract = Layer2AggregatorComponent._extract_topic

    mock_message = MagicMock()
    mock_message.get_topic.return_value = (
        "verdictcouncil/aggregator/input/evidence-analysis"
    )
    assert extract(mock_message) == "verdictcouncil/aggregator/input/evidence-analysis"

    class Plain:
        topic = "verdictcouncil/aggregator/input/fact-reconstruction"

    plain = Plain()
    # getattr(plain, "get_topic", None) finds nothing callable, falls through to .topic
    assert extract(plain) == "verdictcouncil/aggregator/input/fact-reconstruction"

    assert extract(None) == ""


def test_invoke_drops_messages_on_unmapped_topic(caplog):
    component = _build_component()

    message = MagicMock()
    message.get_topic.return_value = "some/other/topic"
    payload = {
        "case_id": "c-1",
        "run_id": "r-1",
        "output": {"evidence_analysis": {}},
        "base_state": {"case_id": "c-1"},
    }

    with caplog.at_level("WARNING"):
        result = component.invoke(message, payload)

    assert result is None
    assert any("unmapped topic" in rec.getMessage() for rec in caplog.records)


def test_invoke_returns_none_when_barrier_not_met():
    component = _build_component()

    # Stub the async aggregator bridge to simulate "not yet ready"
    fake_future = MagicMock()
    fake_future.result.return_value = None
    with patch(
        "src.services.layer2_aggregator.component.asyncio.run_coroutine_threadsafe",
        return_value=fake_future,
    ) as sched:
        message = MagicMock()
        message.get_topic.return_value = (
            "verdictcouncil/aggregator/input/evidence-analysis"
        )
        payload = {
            "case_id": "c-1",
            "run_id": "r-1",
            "output": {"evidence_analysis": {"exhibits": []}},
            "base_state": {"case_id": "c-1"},
        }
        result = component.invoke(message, payload)

    assert result is None
    sched.assert_called_once()


def test_invoke_returns_routed_payload_when_barrier_met():
    component = _build_component()

    merged = {
        "case_id": "c-1",
        "run_id": "r-1",
        "evidence_analysis": {"exhibits": []},
        "extracted_facts": {"timeline": []},
        "witnesses": {"statements": []},
    }
    fake_future = MagicMock()
    fake_future.result.return_value = merged
    with patch(
        "src.services.layer2_aggregator.component.asyncio.run_coroutine_threadsafe",
        return_value=fake_future,
    ):
        message = MagicMock()
        message.get_topic.return_value = (
            "verdictcouncil/aggregator/input/witness-analysis"
        )
        payload = {
            "case_id": "c-1",
            "run_id": "r-1",
            "output": {"witnesses": {"statements": []}},
            "base_state": {"case_id": "c-1"},
        }
        result = component.invoke(message, payload)

    assert result == {
        "payload": merged,
        "topic": "verdictcouncil/a2a/v1/agent/request/legal-knowledge",
    }


def test_invoke_logs_and_drops_malformed_payload(caplog):
    component = _build_component()

    message = MagicMock()
    message.get_topic.return_value = (
        "verdictcouncil/aggregator/input/evidence-analysis"
    )
    # Missing output entirely
    payload = {"case_id": "c-1", "run_id": "r-1"}

    with caplog.at_level("ERROR"):
        result = component.invoke(message, payload)

    assert result is None
    assert any("Malformed aggregator input" in rec.getMessage() for rec in caplog.records)
