"""SAM App subclass that hosts the Layer-2 fan-in aggregator as a Solace service.

This mirrors the shape of `solace_agent_mesh.agent.sac.app.SamAgentApp`:
the class reads `app_config` from YAML, subscribes to the aggregator's
native A2A response wildcard, and injects a single
`Layer2AggregatorComponent` that owns the Redis-backed barrier.

YAML contract (see `configs/services/layer2-aggregator.yaml`):

    apps:
      - name: layer2-aggregator
        app_module: src.services.layer2_aggregator.app
        broker:
          <<: *broker_connection
        app_config:
          namespace: ${NAMESPACE}
          service_name: layer2-aggregator
          response_subscription_topic: "${NAMESPACE}/a2a/v1/agent/response/layer2-aggregator/>"
          redis_url: ${REDIS_URL}
"""

from __future__ import annotations

from typing import Any, Dict

from solace_ai_connector.common.log import log
from solace_ai_connector.flow.app import App

from .component import Layer2AggregatorComponent


info = {
    "class_name": "Layer2AggregatorApp",
    "description": (
        "SAM service that fans in the three parallel Layer-2 agents "
        "(evidence-analysis, fact-reconstruction, witness-analysis) "
        "for the VerdictCouncil pipeline."
    ),
}


class Layer2AggregatorApp(App):
    """Hosts a single `Layer2AggregatorComponent` with SAM-style config wiring."""

    app_schema: Dict[str, Any] = {}

    def __init__(self, app_info: Dict[str, Any], **kwargs: Any) -> None:
        app_config = app_info.get("app_config") or {}

        namespace = app_config.get("namespace")
        service_name = app_config.get("service_name", "layer2-aggregator")
        response_subscription_topic = app_config.get("response_subscription_topic")
        redis_url = app_config.get("redis_url")

        if not namespace:
            raise ValueError("Layer2AggregatorApp requires app_config.namespace")
        if not response_subscription_topic:
            raise ValueError(
                "Layer2AggregatorApp requires app_config.response_subscription_topic"
            )
        if not redis_url:
            raise ValueError("Layer2AggregatorApp requires app_config.redis_url")

        log.info(
            "Configuring Layer2AggregatorApp '%s' in namespace '%s' subscribing to %s",
            service_name,
            namespace,
            response_subscription_topic,
        )

        component_definition = {
            "component_name": f"{service_name}_component",
            "component_class": Layer2AggregatorComponent,
            "component_config": {
                "redis_url": redis_url,
            },
            "subscriptions": [{"topic": response_subscription_topic}],
        }
        app_info["components"] = [component_definition]

        broker_config = app_info.setdefault("broker", {})
        broker_config["input_enabled"] = True
        broker_config["output_enabled"] = True
        broker_config["queue_name"] = f"{namespace.strip('/')}/q/services/{service_name}"
        broker_config["temporary_queue"] = True

        super().__init__(app_info, **kwargs)
        log.info("Layer2AggregatorApp '%s' initialization complete", service_name)
