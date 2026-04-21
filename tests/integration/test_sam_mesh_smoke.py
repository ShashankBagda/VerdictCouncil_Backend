"""Live SAM mesh smoke tests against a real Solace broker.

Scope (Phase 4, narrow variant):
- Real Solace broker (docker-compose.infra.yml on localhost:55555).
- Real `SolaceA2AClient` as the mesh runner.
- An in-process fake "agent" subscribed to the request-topic wildcard
  that echoes each SendTaskRequest back as a SendTaskResponse on the
  message's ``replyTo`` property.

This validates the parts most likely to break and that unit tests
can't catch: the Solace client's publish/subscribe wiring, the A2A
topic convention, JSON envelope integrity on the wire, and the
JSON-RPC id-based correlation in ``await_response``.

Gated two ways so it never runs in default CI:
- ``INTEGRATION_TESTS=1`` (infra must be up).
- ``pytest -m mesh_smoke``.

Out of scope for this file (run via a separate target when needed):
- Full 9-agent pipeline with the Layer-2 aggregator running —
  requires the aggregator SAM service up under `honcho start`.
- Real SAM agent subprocesses — would require OpenAI credentials.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from src.pipeline._a2a_client import build_send_task_request, new_task_id
from src.pipeline._solace_a2a_client import REPLY_TO_PROPERTY, SolaceA2AClient
from src.services.layer2_aggregator.a2a import (
    build_send_task_response,
    parse_send_task_response,
)

logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.mesh_smoke,
    pytest.mark.skipif(
        os.environ.get("INTEGRATION_TESTS") != "1",
        reason="Live mesh smoke requires INTEGRATION_TESTS=1 and Solace on localhost:55555",
    ),
]

# Defaults match docker-compose.infra.yml — Solace ships with the `default`
# VPN and an `admin`/`admin` user. No bootstrap script yet creates the
# `verdictcouncil` VPN / `vc-agent` user, so the smoke test connects as
# admin to the default VPN. The namespace prefix is what the production
# config uses — we keep it here so topic shapes are identical on the wire.
BROKER_URL = os.environ.get("SOLACE_SMOKE_URL", "tcp://localhost:55555")
BROKER_VPN = os.environ.get("SOLACE_SMOKE_VPN", "default")
BROKER_USER = os.environ.get("SOLACE_SMOKE_USER", "admin")
BROKER_PASSWORD = os.environ.get("SOLACE_SMOKE_PASSWORD", "admin")
NAMESPACE = os.environ.get("SOLACE_SMOKE_NAMESPACE", "verdictcouncil")


def _broker_reachable(url: str, timeout: float = 1.0) -> bool:
    # tcp://host:port → (host, port)
    try:
        _, hostport = url.split("://", 1)
        host, port_s = hostport.split(":", 1)
        port = int(port_s)
    except ValueError:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module", autouse=True)
def _require_broker() -> None:
    if not _broker_reachable(BROKER_URL):
        pytest.skip(
            f"Solace broker not reachable at {BROKER_URL} — "
            "run `make infra-up` before running mesh smoke tests"
        )


@asynccontextmanager
async def _echo_subscriber(
    namespace: str,
) -> AsyncIterator[dict]:
    """Spin up a Solace receiver that echoes SendTaskRequests as responses.

    Subscribes to the wildcard `{namespace}/a2a/v1/agent/request/>` and,
    for each inbound message, extracts the `replyTo` property and the
    JSON-RPC `id`, then publishes a `SendTaskResponse` back to `replyTo`
    with the original request's DataPart payload echoed in the response's
    DataPart. Yields a stats dict so tests can assert received counts.
    """
    from solace.messaging.config.solace_properties import (
        authentication_properties,
        service_properties,
        transport_layer_properties,
    )
    from solace.messaging.messaging_service import MessagingService
    from solace.messaging.receiver.inbound_message import InboundMessage
    from solace.messaging.receiver.message_receiver import MessageHandler
    from solace.messaging.resources.topic import Topic
    from solace.messaging.resources.topic_subscription import TopicSubscription

    stats: dict = {"received": 0, "published": 0, "topics": []}
    loop = asyncio.get_running_loop()

    broker_props = {
        transport_layer_properties.HOST: BROKER_URL,
        service_properties.VPN_NAME: BROKER_VPN,
        authentication_properties.SCHEME_BASIC_USER_NAME: BROKER_USER,
        authentication_properties.SCHEME_BASIC_PASSWORD: BROKER_PASSWORD,
    }
    service = MessagingService.builder().from_properties(broker_props).build()
    await asyncio.to_thread(service.connect)

    publisher = service.create_direct_message_publisher_builder().build()
    await asyncio.to_thread(publisher.start)

    wildcard = f"{namespace}/a2a/v1/agent/request/>"
    receiver = (
        service.create_direct_message_receiver_builder()
        .with_subscriptions([TopicSubscription.of(wildcard)])
        .build()
    )
    await asyncio.to_thread(receiver.start)

    def _handle_inbound(message: InboundMessage) -> None:
        try:
            # Publishers use `build(str)`, so read the string slot first.
            payload_str = message.get_payload_as_string()
            if payload_str:
                envelope = json.loads(payload_str)
            else:
                body_bytes = message.get_payload_as_bytes() or b""
                envelope = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
            topic = message.get_destination_name()
            reply_to = message.get_property(REPLY_TO_PROPERTY)
        except Exception as exc:  # noqa: BLE001 — diagnostic only
            logger.error("echo subscriber failed to parse inbound: %s", exc)
            return

        task_id = envelope.get("id")
        session_id = (envelope.get("params") or {}).get("sessionId")
        parts = ((envelope.get("params") or {}).get("message") or {}).get("parts") or []
        payload = {}
        for p in parts:
            if isinstance(p, dict) and p.get("type") == "data":
                data = p.get("data")
                if isinstance(data, dict):
                    payload = data
                    break

        if not task_id or not reply_to:
            logger.warning(
                "echo subscriber skipping envelope (id=%s reply_to=%s)",
                task_id,
                reply_to,
            )
            return

        response = build_send_task_response(task_id, session_id, payload)
        # SolaceA2AClient has a matching note: build(bytes) fails on
        # this SDK build; build(str) works. JSON envelopes are ASCII.
        outbound = service.message_builder().build(json.dumps(response))

        def _record() -> None:
            stats["received"] += 1
            stats["topics"].append(topic)

        loop.call_soon_threadsafe(_record)

        try:
            publisher.publish(outbound, Topic.of(reply_to))
        except Exception as exc:  # noqa: BLE001 — diagnostic only
            logger.error("echo subscriber publish failed: %s", exc)
            return

        loop.call_soon_threadsafe(lambda: stats.__setitem__("published", stats["published"] + 1))

    class _Handler(MessageHandler):
        def on_message(self, message: InboundMessage) -> None:
            _handle_inbound(message)

    receiver.receive_async(_Handler())

    try:
        yield stats
    finally:
        try:
            await asyncio.to_thread(receiver.terminate, 3000)
        except Exception as exc:  # noqa: BLE001
            logger.warning("echo receiver terminate failed: %s", exc)
        try:
            await asyncio.to_thread(publisher.terminate, 3000)
        except Exception as exc:  # noqa: BLE001
            logger.warning("echo publisher terminate failed: %s", exc)
        try:
            await asyncio.to_thread(service.disconnect)
        except Exception as exc:  # noqa: BLE001
            logger.warning("echo service disconnect failed: %s", exc)


@asynccontextmanager
async def _mesh_client() -> AsyncIterator[SolaceA2AClient]:
    client = SolaceA2AClient(
        broker_url=BROKER_URL,
        vpn_name=BROKER_VPN,
        username=BROKER_USER,
        password=BROKER_PASSWORD,
        namespace=NAMESPACE,
    )
    await client.connect()
    try:
        yield client
    finally:
        await client.close()


class TestSingleAgentRoundTrip:
    """Tier (i): publish → echo → response, end-to-end over a real broker."""

    @pytest.mark.asyncio
    async def test_publish_await_response_roundtrip(self) -> None:
        async with _echo_subscriber(NAMESPACE) as stats, _mesh_client() as client:
            # Solace subscriptions propagate slightly async — give the
            # broker a beat so the receiver is bound before we publish.
            await asyncio.sleep(0.5)

            task_id = new_task_id("case_processing-smoke")
            session_id = "session-smoke-1"
            payload = {
                "case_id": "00000000-0000-0000-0000-000000000001",
                "smoke_marker": "tier-i-roundtrip",
                "agent_name": "case_processing",
            }
            envelope = build_send_task_request(task_id, session_id, payload)
            reply_to = f"{NAMESPACE}/a2a/v1/agent/response/mesh-runner/{task_id}"

            request_topic = f"{NAMESPACE}/a2a/v1/agent/request/case_processing"
            await client.publish(request_topic, envelope, reply_to=reply_to)

            response = await client.await_response(task_id, timeout=5.0)

        assert response["id"] == task_id
        assert response["result"]["status"]["state"] == "completed"

        echoed = parse_send_task_response(response)
        assert echoed == payload, "DataPart payload must round-trip unchanged"
        assert stats["received"] >= 1
        assert stats["published"] >= 1
        assert any(t.endswith("/agent/request/case_processing") for t in stats["topics"])

    @pytest.mark.asyncio
    async def test_parallel_publishes_correlate_by_task_id(self) -> None:
        """Two concurrent requests must resolve on their own task ids, not swap."""
        async with _echo_subscriber(NAMESPACE), _mesh_client() as client:
            await asyncio.sleep(0.5)

            async def one_roundtrip(agent: str, marker: str) -> dict:
                task_id = new_task_id(f"{agent}-smoke")
                envelope = build_send_task_request(task_id, "session-smoke-2", {"marker": marker})
                reply_to = f"{NAMESPACE}/a2a/v1/agent/response/mesh-runner/{task_id}"
                await client.publish(
                    f"{NAMESPACE}/a2a/v1/agent/request/{agent}",
                    envelope,
                    reply_to=reply_to,
                )
                resp = await client.await_response(task_id, timeout=5.0)
                return parse_send_task_response(resp)

            results = await asyncio.gather(
                one_roundtrip("evidence_analysis", "marker-ev"),
                one_roundtrip("fact_reconstruction", "marker-fact"),
                one_roundtrip("witness_analysis", "marker-wit"),
            )

        markers = {r.get("marker") for r in results}
        assert markers == {"marker-ev", "marker-fact", "marker-wit"}
