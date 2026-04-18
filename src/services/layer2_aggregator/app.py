"""SAM App subclass that hosts the Layer-2 fan-in aggregator as a Solace service.

This mirrors the shape of `solace_agent_mesh.agent.sac.app.SamAgentApp`: the
class reads `app_config` from YAML, builds the broker subscription list from
`subscriptions[*].topic`, injects a single `Layer2AggregatorComponent`, and
enables broker input + output so the merged `CaseState` is published on
`app_config.output_topic`.

YAML contract (see `configs/services/layer2-aggregator.yaml`):

    apps:
      - name: layer2-aggregator
        app_module: src.services.layer2_aggregator.app
        broker:
          <<: *broker_connection
        app_config:
          namespace: ${NAMESPACE}
          service_name: Layer2Aggregator
          subscriptions:
            - {topic: "...evidence-analysis", agent_key: "evidence_analysis"}
            - {topic: "...fact-reconstruction", agent_key: "extracted_facts"}
            - {topic: "...witness-analysis", agent_key: "witnesses"}
          output_topic: "verdictcouncil/a2a/v1/agent/request/legal-knowledge"
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
        subscriptions = app_config.get("subscriptions") or []
        output_topic = app_config.get("output_topic")
        redis_url = app_config.get("redis_url")

        if not namespace:
            raise ValueError("Layer2AggregatorApp requires app_config.namespace")
        if not subscriptions:
            raise ValueError(
                "Layer2AggregatorApp requires app_config.subscriptions (at least one)"
            )
        if not output_topic:
            raise ValueError("Layer2AggregatorApp requires app_config.output_topic")
        if not redis_url:
            raise ValueError("Layer2AggregatorApp requires app_config.redis_url")

        topic_map: Dict[str, str] = {}
        broker_subscriptions = []
        for sub in subscriptions:
            topic = sub.get("topic")
            agent_key = sub.get("agent_key")
            if not (topic and agent_key):
                raise ValueError(
                    f"Each subscription needs both 'topic' and 'agent_key' (got {sub!r})"
                )
            topic_map[topic] = agent_key
            broker_subscriptions.append({"topic": topic})

        log.info(
            "Configuring Layer2AggregatorApp '%s' in namespace '%s' with %d subscriptions",
            service_name,
            namespace,
            len(broker_subscriptions),
        )

        component_definition = {
            "component_name": f"{service_name}_component",
            "component_class": Layer2AggregatorComponent,
            "component_config": {
                "topic_map": topic_map,
                "output_topic": output_topic,
                "redis_url": redis_url,
            },
            "subscriptions": broker_subscriptions,
        }
        app_info["components"] = [component_definition]

        broker_config = app_info.setdefault("broker", {})
        broker_config["input_enabled"] = True
        broker_config["output_enabled"] = True
        broker_config["queue_name"] = f"{namespace.strip('/')}/q/services/{service_name}"
        broker_config["temporary_queue"] = True

        super().__init__(app_info, **kwargs)
        log.info("Layer2AggregatorApp '%s' initialization complete", service_name)
