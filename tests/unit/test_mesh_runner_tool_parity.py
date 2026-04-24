"""Verify legal-knowledge agent YAML has both required RAG tools bound.

This guards against accidental removal of either tool from the config.
"""

from pathlib import Path

from src.pipeline.runner import _load_yaml_with_includes

CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "configs" / "agents"

REQUIRED_TOOLS = {"SearchPrecedentsTool", "SearchDomainGuidanceTool"}


def _extract_tool_class_names(raw: dict) -> set[str]:
    apps = raw.get("apps", [])
    if not apps:
        return set()
    app_config = apps[0].get("app_config", {})
    tools = app_config.get("tools", [])
    return {t["class_name"] for t in tools if isinstance(t, dict) and "class_name" in t}


class TestLegalKnowledgeToolParity:
    def test_legal_knowledge_yaml_loads(self):
        config_path = CONFIGS_DIR / "legal-knowledge.yaml"
        assert config_path.exists(), f"Config missing: {config_path}"
        raw = _load_yaml_with_includes(config_path)
        assert raw is not None

    def test_legal_knowledge_has_search_precedents_tool(self):
        raw = _load_yaml_with_includes(CONFIGS_DIR / "legal-knowledge.yaml")
        tool_classes = _extract_tool_class_names(raw)
        assert "SearchPrecedentsTool" in tool_classes, (
            f"legal-knowledge is missing SearchPrecedentsTool; found: {tool_classes}"
        )

    def test_legal_knowledge_has_search_domain_guidance_tool(self):
        raw = _load_yaml_with_includes(CONFIGS_DIR / "legal-knowledge.yaml")
        tool_classes = _extract_tool_class_names(raw)
        assert "SearchDomainGuidanceTool" in tool_classes, (
            f"legal-knowledge is missing SearchDomainGuidanceTool; found: {tool_classes}"
        )

    def test_legal_knowledge_has_all_required_tools(self):
        raw = _load_yaml_with_includes(CONFIGS_DIR / "legal-knowledge.yaml")
        tool_classes = _extract_tool_class_names(raw)
        missing = REQUIRED_TOOLS - tool_classes
        assert not missing, f"legal-knowledge missing required tools: {missing}"

    def test_search_domain_guidance_tool_module_path(self):
        raw = _load_yaml_with_includes(CONFIGS_DIR / "legal-knowledge.yaml")
        apps = raw.get("apps", [])
        tools = apps[0].get("app_config", {}).get("tools", []) if apps else []
        domain_tool = next(
            (t for t in tools if t.get("class_name") == "SearchDomainGuidanceTool"),
            None,
        )
        assert domain_tool is not None
        assert domain_tool.get("component_module") == "src.tools.sam.search_domain_guidance_tool"

    def test_search_precedents_tool_module_path(self):
        raw = _load_yaml_with_includes(CONFIGS_DIR / "legal-knowledge.yaml")
        apps = raw.get("apps", [])
        tools = apps[0].get("app_config", {}).get("tools", []) if apps else []
        precedents_tool = next(
            (t for t in tools if t.get("class_name") == "SearchPrecedentsTool"),
            None,
        )
        assert precedents_tool is not None
        assert precedents_tool.get("component_module") == "src.tools.sam.search_precedents_tool"
