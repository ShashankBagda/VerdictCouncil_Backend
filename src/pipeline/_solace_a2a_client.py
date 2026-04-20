"""Solace-backed A2AClient for the mesh pipeline runner.

Wraps `solace.messaging` direct messaging as the async `A2AClient`
Protocol defined in `_a2a_client.py`. Inbound messages arrive on a
Solace receiver thread; they are dispatched to the asyncio event loop
via `loop.call_soon_threadsafe`.

Topics follow the VerdictCouncil A2A convention:
- Publish:  ``{namespace}/a2a/v1/agent/request/<agent>``
- Reply-to: ``{namespace}/a2a/v1/agent/response/<mesh_runner_name>/<task_id>``

The client subscribes once to the mesh-runner response wildcard
(``{namespace}/a2a/v1/agent/response/<mesh_runner_name>/>``) and
correlates inbound envelopes to pending requests by the JSON-RPC
``id`` field.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from solace.messaging.config.retry_strategy import RetryStrategy
from solace.messaging.config.solace_properties import (
    authentication_properties,
    service_properties,
    transport_layer_properties,
)
from solace.messaging.messaging_service import MessagingService
from solace.messaging.publisher.direct_message_publisher import (
    DirectMessagePublisher,
)
from solace.messaging.receiver.direct_message_receiver import (
    DirectMessageReceiver,
)
from solace.messaging.receiver.inbound_message import InboundMessage
from solace.messaging.receiver.message_receiver import MessageHandler
from solace.messaging.resources.topic import Topic
from solace.messaging.resources.topic_subscription import TopicSubscription

logger = logging.getLogger(__name__)

REPLY_TO_PROPERTY = "replyTo"


class SolaceA2AClient:
    """Async A2AClient backed by a single Solace direct-messaging session.

    One MessagingService, one publisher, one receiver wildcard-subscribed to
    the mesh-runner's response topic. Thread-safe: inbound Solace callbacks
    hop to the asyncio loop before resolving pending futures.
    """

    def __init__(
        self,
        *,
        broker_url: str,
        vpn_name: str,
        username: str,
        password: str,
        namespace: str,
        mesh_runner_name: str = "mesh-runner",
        reconnect_retries: int = 20,
        reconnect_interval_ms: int = 3000,
    ) -> None:
        self._broker_url = broker_url
        self._vpn_name = vpn_name
        self._username = username
        self._password = password
        self._namespace = namespace
        self._mesh_runner_name = mesh_runner_name
        self._reconnect_retries = reconnect_retries
        self._reconnect_interval_ms = reconnect_interval_ms

        self._service: MessagingService | None = None
        self._publisher: DirectMessagePublisher | None = None
        self._receiver: DirectMessageReceiver | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        self._pending: dict[str, asyncio.Future[dict]] = {}
        self._delivered: dict[str, dict] = {}
        self._pending_lock: asyncio.Lock | None = None

    async def connect(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._pending_lock = asyncio.Lock()

        broker_props: dict[str, Any] = {
            transport_layer_properties.HOST: self._broker_url,
            service_properties.VPN_NAME: self._vpn_name,
            authentication_properties.SCHEME_BASIC_USER_NAME: self._username,
            authentication_properties.SCHEME_BASIC_PASSWORD: self._password,
        }
        service = (
            MessagingService.builder()
            .from_properties(broker_props)
            .with_reconnection_retry_strategy(
                RetryStrategy.parametrized_retry(
                    retries=self._reconnect_retries,
                    retry_interval=self._reconnect_interval_ms,
                )
            )
            .build()
        )
        await asyncio.to_thread(service.connect)
        self._service = service

        publisher = service.create_direct_message_publisher_builder().build()
        await asyncio.to_thread(publisher.start)
        self._publisher = publisher

        wildcard = (
            f"{self._namespace}/a2a/v1/agent/response/{self._mesh_runner_name}/>"
        )
        receiver = (
            service.create_direct_message_receiver_builder()
            .with_subscriptions([TopicSubscription.of(wildcard)])
            .build()
        )
        await asyncio.to_thread(receiver.start)
        receiver.receive_async(_ReplyHandler(self))
        self._receiver = receiver

        logger.info(
            "SolaceA2AClient connected host=%s vpn=%s subscribed=%s",
            self._broker_url,
            self._vpn_name,
            wildcard,
        )

    async def close(self) -> None:
        if self._receiver is not None:
            try:
                await asyncio.to_thread(self._receiver.terminate, 5000)
            except Exception as exc:
                logger.warning("Solace receiver terminate failed: %s", exc)
            self._receiver = None
        if self._publisher is not None:
            try:
                await asyncio.to_thread(self._publisher.terminate, 5000)
            except Exception as exc:
                logger.warning("Solace publisher terminate failed: %s", exc)
            self._publisher = None
        if self._service is not None:
            try:
                await asyncio.to_thread(self._service.disconnect)
            except Exception as exc:
                logger.warning("Solace service disconnect failed: %s", exc)
            self._service = None

        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()
        self._delivered.clear()

    async def publish(
        self,
        topic: str,
        envelope: dict,
        reply_to: str | None = None,
    ) -> None:
        if self._publisher is None or self._service is None:
            raise RuntimeError("SolaceA2AClient.publish called before connect()")
        # NOTE: `message_builder().build()` fails on bytes in the
        # installed Solace Python SDK (Fail / SOLCLIENT_SUBCODE_OK — a
        # confusing surface error). Pass str so the SDK takes the
        # string-attachment branch. json.dumps defaults to ASCII-safe
        # output, so the JSON envelope survives the encode round-trip.
        body = json.dumps(envelope)
        builder = self._service.message_builder()
        if reply_to:
            builder = builder.with_property(REPLY_TO_PROPERTY, reply_to)
        outbound = builder.build(body)
        await asyncio.to_thread(self._publisher.publish, outbound, Topic.of(topic))

    async def await_response(self, task_id: str, timeout: float) -> dict:
        if self._pending_lock is None:
            raise RuntimeError("SolaceA2AClient.await_response called before connect()")
        async with self._pending_lock:
            if task_id in self._delivered:
                return self._delivered.pop(task_id)
            fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
            self._pending[task_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(task_id, None)

    def _deliver_on_loop(self, task_id: str, envelope: dict) -> None:
        """Resolve a pending future or buffer the envelope. Runs on the asyncio loop."""
        fut = self._pending.get(task_id)
        if fut is not None and not fut.done():
            fut.set_result(envelope)
            return
        self._delivered[task_id] = envelope


class _ReplyHandler(MessageHandler):
    """Solace receiver callback: parse JSON, hop to the loop, resolve future."""

    def __init__(self, client: SolaceA2AClient) -> None:
        self._client = client

    def on_message(self, message: InboundMessage) -> None:
        # Prefer string payload: our publisher sends via `build(str)`
        # because `build(bytes)` silently fails on this SDK version.
        # When the sender uses the string slot, `get_payload_as_bytes`
        # does not return the string — only `get_payload_as_string` does.
        try:
            payload_str = message.get_payload_as_string()
            if payload_str:
                envelope = json.loads(payload_str)
            else:
                payload_bytes = message.get_payload_as_bytes()
                envelope = (
                    json.loads(payload_bytes.decode("utf-8"))
                    if payload_bytes
                    else None
                )
        except Exception as exc:
            logger.error("SolaceA2AClient failed to parse inbound envelope: %s", exc)
            return
        if not isinstance(envelope, dict):
            logger.error("SolaceA2AClient got non-dict envelope type=%s", type(envelope))
            return
        task_id = envelope.get("id")
        if not task_id:
            logger.warning(
                "SolaceA2AClient got envelope with no id (keys=%s); dropping",
                list(envelope.keys()),
            )
            return
        loop = self._client._loop
        if loop is None:
            logger.error("SolaceA2AClient received message before connect() completed")
            return
        loop.call_soon_threadsafe(self._client._deliver_on_loop, task_id, envelope)
