"""Unit tests for the SAM wrapper around Layer2Aggregator.

Exercises the config-wiring in `src/services/layer2_aggregator/app.py`
and the native-A2A invoke path in `component.py`. The Redis barrier
itself is covered by `tests/unit/test_layer2_aggregator.py`.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.services.layer2_aggregator.a2a import (
    build_send_task_response,
    parse_send_task_response,
    sub_task_id_from_topic,
)
from src.services.layer2_aggregator.app import Layer2AggregatorApp
from src.services.layer2_aggregator.component import Layer2AggregatorComponent

# ---------------------------------------------------------------------------
# Layer2AggregatorApp — config validation + component wiring
# ---------------------------------------------------------------------------


def _app_info(**app_config_overrides):
    base_app_config = {
        "namespace": "verdictcouncil",
        "service_name": "layer2-aggregator",
        "response_subscription_topic": "verdictcouncil/a2a/v1/agent/response/layer2-aggregator/>",
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


def test_app_injects_single_component_with_redis_url():
    app_info = _app_info()
    _, _, resolved = _build_app(app_info)

    components = resolved["components"]
    assert len(components) == 1
    component_def = components[0]

    assert component_def["component_class"] is Layer2AggregatorComponent
    assert component_def["component_name"] == "layer2-aggregator_component"
    assert component_def["component_config"] == {"redis_url": "redis://localhost:6379/1"}


def test_app_sets_broker_subscription_to_response_wildcard():
    app_info = _app_info()
    _, _, resolved = _build_app(app_info)

    broker_subs = resolved["components"][0]["subscriptions"]
    assert broker_subs == [{"topic": "verdictcouncil/a2a/v1/agent/response/layer2-aggregator/>"}]


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
    ["namespace", "response_subscription_topic", "redis_url"],
)
def test_app_rejects_missing_required_field(missing_field):
    app_info = _app_info()
    app_info["app_config"][missing_field] = ""

    with pytest.raises(ValueError, match=missing_field):
        _build_app(app_info)


# ---------------------------------------------------------------------------
# a2a helpers
# ---------------------------------------------------------------------------


def test_sub_task_id_from_topic_returns_trailing_segment():
    assert (
        sub_task_id_from_topic("verdictcouncil/a2a/v1/agent/response/layer2-aggregator/task-abc123")
        == "task-abc123"
    )
    assert sub_task_id_from_topic("") == ""


def test_parse_send_task_response_extracts_data_part():
    envelope = {
        "jsonrpc": "2.0",
        "id": "task-1",
        "result": {
            "id": "task-1",
            "status": {
                "state": "completed",
                "message": {
                    "role": "agent",
                    "parts": [{"type": "data", "data": {"evidence_analysis": {"exhibits": []}}}],
                },
                "timestamp": "2026-04-18T00:00:00Z",
            },
        },
    }
    assert parse_send_task_response(envelope) == {"evidence_analysis": {"exhibits": []}}
    assert parse_send_task_response(json.dumps(envelope).encode()) == {
        "evidence_analysis": {"exhibits": []}
    }
    assert parse_send_task_response(json.dumps(envelope)) == {"evidence_analysis": {"exhibits": []}}


def test_parse_send_task_response_falls_back_to_text_part_json():
    envelope = {
        "result": {
            "status": {
                "message": {
                    "role": "agent",
                    "parts": [{"type": "text", "text": '{"witnesses": {"statements": []}}'}],
                }
            }
        }
    }
    assert parse_send_task_response(envelope) == {"witnesses": {"statements": []}}


def test_parse_send_task_response_returns_empty_on_garbage():
    assert parse_send_task_response(None) == {}
    assert parse_send_task_response(42) == {}
    assert parse_send_task_response(b"not json") == {}
    assert parse_send_task_response({"result": {}}) == {}


def test_build_send_task_response_shape():
    merged = {"case_id": "c-1", "evidence_analysis": {}, "extracted_facts": {}, "witnesses": {}}
    env = build_send_task_response(task_id="layer2-c-1-r-1", session_id="r-1", merged_state=merged)

    assert env["jsonrpc"] == "2.0"
    assert env["id"] == "layer2-c-1-r-1"
    result = env["result"]
    assert result["id"] == "layer2-c-1-r-1"
    assert result["sessionId"] == "r-1"
    assert result["status"]["state"] == "completed"
    parts = result["status"]["message"]["parts"]
    assert parts == [{"type": "data", "data": merged}]
    # Timestamp is ISO 8601 parseable
    datetime.fromisoformat(result["status"]["timestamp"])


# ---------------------------------------------------------------------------
# Layer2AggregatorComponent — invoke path
# ---------------------------------------------------------------------------


def _build_component():
    """Instantiate the component without booting SAI or the asyncio loop thread.

    Tests patch `asyncio.run_coroutine_threadsafe` to resolve coroutines
    synchronously against a new event loop, so the real loop thread
    isn't needed.
    """
    component = object.__new__(Layer2AggregatorComponent)
    component.component_config = {"redis_url": "redis://localhost:6379/1"}
    component._redis = MagicMock()
    component._aggregator = MagicMock()
    component._loop = MagicMock()
    return component


class _AsyncResolveFuture:
    """Resolves run_coroutine_threadsafe by awaiting the coro on a fresh loop.

    Avoids needing a real loop thread in tests while keeping the
    `.result()` contract invoke() relies on.
    """

    def __init__(self, coro):
        self._coro = coro

    def result(self, timeout=None):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._coro)
        finally:
            loop.close()


def _resolve(coro, _loop):
    return _AsyncResolveFuture(coro)


def _response_envelope(output: dict, sub_task_id: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": sub_task_id,
        "result": {
            "id": sub_task_id,
            "status": {
                "state": "completed",
                "message": {"role": "agent", "parts": [{"type": "data", "data": output}]},
                "timestamp": "2026-04-18T00:00:00Z",
            },
        },
    }


def _message_on(topic: str) -> MagicMock:
    msg = MagicMock()
    msg.get_topic.return_value = topic
    return msg


async def _as_async(value):
    return value


def test_invoke_drops_message_missing_sub_task_id(caplog):
    component = _build_component()
    with caplog.at_level("WARNING"):
        result = component.invoke(_message_on(""), _response_envelope({}, ""))
    assert result is None
    assert any("no sub_task_id" in rec.getMessage() for rec in caplog.records)


def test_invoke_drops_orphan_response_with_no_correlation(caplog):
    component = _build_component()
    component._redis.get = MagicMock(return_value=_as_async(None))

    topic = "verdictcouncil/a2a/v1/agent/response/layer2-aggregator/orphan-123"
    with (
        patch(
            "src.services.layer2_aggregator.component.asyncio.run_coroutine_threadsafe",
            side_effect=_resolve,
        ),
        caplog.at_level("WARNING"),
    ):
        result = component.invoke(
            _message_on(topic),
            _response_envelope({"evidence_analysis": {}}, "orphan-123"),
        )

    assert result is None
    assert any("orphan response" in rec.getMessage() for rec in caplog.records)


def test_invoke_drops_empty_agent_output(caplog):
    component = _build_component()

    async def get(key):
        key_str = key if isinstance(key, str) else key.decode()
        if key_str.startswith("vc:aggregator:sub_task:"):
            return b"evidence_analysis|c-1|r-1"
        return None

    component._redis.get = get

    topic = "verdictcouncil/a2a/v1/agent/response/layer2-aggregator/task-1"
    empty_envelope = {"jsonrpc": "2.0", "id": "task-1", "result": {}}
    with (
        patch(
            "src.services.layer2_aggregator.component.asyncio.run_coroutine_threadsafe",
            side_effect=_resolve,
        ),
        caplog.at_level("ERROR"),
    ):
        result = component.invoke(_message_on(topic), empty_envelope)

    assert result is None
    assert any("unparseable" in rec.getMessage() for rec in caplog.records)


def test_invoke_errors_when_run_meta_missing(caplog):
    component = _build_component()

    async def get(key):
        key_str = key if isinstance(key, str) else key.decode()
        if key_str.startswith("vc:aggregator:sub_task:"):
            return b"evidence_analysis|c-1|r-1"
        if key_str.startswith("vc:aggregator:run:"):
            return None
        return None

    component._redis.get = get

    topic = "verdictcouncil/a2a/v1/agent/response/layer2-aggregator/task-1"
    envelope = _response_envelope({"evidence_analysis": {"exhibits": []}}, "task-1")
    with (
        patch(
            "src.services.layer2_aggregator.component.asyncio.run_coroutine_threadsafe",
            side_effect=_resolve,
        ),
        caplog.at_level("ERROR"),
    ):
        result = component.invoke(_message_on(topic), envelope)

    assert result is None
    assert any("No run metadata" in rec.getMessage() for rec in caplog.records)


def test_invoke_returns_none_when_barrier_not_met():
    component = _build_component()

    async def get(key):
        key_str = key if isinstance(key, str) else key.decode()
        if key_str.startswith("vc:aggregator:sub_task:"):
            return b"evidence_analysis|c-1|r-1"
        if key_str.startswith("vc:aggregator:run:"):
            return json.dumps(
                {
                    "base_state": {"case_id": "c-1"},
                    "mesh_reply_to": (
                        "verdictcouncil/a2a/v1/agent/response/mesh-runner/layer2-c-1-r-1"
                    ),
                }
            ).encode()
        return None

    component._redis.get = get

    async def receive_output(**_kwargs):
        return None

    component._aggregator.receive_output = receive_output

    topic = "verdictcouncil/a2a/v1/agent/response/layer2-aggregator/task-1"
    envelope = _response_envelope({"evidence_analysis": {"exhibits": []}}, "task-1")
    with patch(
        "src.services.layer2_aggregator.component.asyncio.run_coroutine_threadsafe",
        side_effect=_resolve,
    ):
        result = component.invoke(_message_on(topic), envelope)

    assert result is None


def test_invoke_emits_send_task_response_when_barrier_met():
    component = _build_component()

    merged_state = {
        "case_id": "c-1",
        "evidence_analysis": {"exhibits": []},
        "extracted_facts": {"timeline": []},
        "witnesses": {"statements": []},
    }
    mesh_reply_to = "verdictcouncil/a2a/v1/agent/response/mesh-runner/layer2-c-1-r-1"

    async def get(key):
        key_str = key if isinstance(key, str) else key.decode()
        if key_str.startswith("vc:aggregator:sub_task:"):
            return b"witnesses|c-1|r-1"
        if key_str.startswith("vc:aggregator:run:"):
            return json.dumps(
                {"base_state": {"case_id": "c-1"}, "mesh_reply_to": mesh_reply_to}
            ).encode()
        return None

    component._redis.get = get

    async def receive_output(**_kwargs):
        return merged_state

    component._aggregator.receive_output = receive_output

    topic = "verdictcouncil/a2a/v1/agent/response/layer2-aggregator/task-9"
    envelope = _response_envelope({"witnesses": {"statements": []}}, "task-9")
    with patch(
        "src.services.layer2_aggregator.component.asyncio.run_coroutine_threadsafe",
        side_effect=_resolve,
    ):
        result = component.invoke(_message_on(topic), envelope)

    assert result is not None
    assert result["topic"] == mesh_reply_to
    sent = result["payload"]
    assert sent["jsonrpc"] == "2.0"
    assert sent["id"] == "layer2-c-1-r-1"
    parts = sent["result"]["status"]["message"]["parts"]
    assert parts == [{"type": "data", "data": merged_state}]


def test_invoke_rejects_malformed_correlation_string(caplog):
    component = _build_component()

    async def get(key):
        return b"only-two|fields"  # missing run_id

    component._redis.get = get

    topic = "verdictcouncil/a2a/v1/agent/response/layer2-aggregator/task-bad"
    envelope = _response_envelope({"evidence_analysis": {}}, "task-bad")
    with (
        patch(
            "src.services.layer2_aggregator.component.asyncio.run_coroutine_threadsafe",
            side_effect=_resolve,
        ),
        caplog.at_level("ERROR"),
    ):
        result = component.invoke(_message_on(topic), envelope)

    assert result is None
    assert any("Malformed sub_task correlation" in rec.getMessage() for rec in caplog.records)
