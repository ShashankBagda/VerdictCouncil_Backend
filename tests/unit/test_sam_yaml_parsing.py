"""Unit tests for SAM YAML parsing and agent config migration."""

from pathlib import Path

import pytest
import yaml

from src.pipeline.runner import PipelineRunner, _load_yaml_with_includes

CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "configs" / "agents"

AGENT_NAMES = [
    "case-processing",
    "complexity-routing",
    "evidence-analysis",
    "fact-reconstruction",
    "witness-analysis",
    "legal-knowledge",
    "argument-construction",
    "deliberation",
    "governance-verdict",
]

# Expected model anchors per agent (based on model_tier mapping)
EXPECTED_MODELS = {
    "case-processing": "gpt54_nano_model",         # lightweight
    "complexity-routing": "gpt54_nano_model",       # lightweight
    "evidence-analysis": "gpt5_model",              # strong
    "fact-reconstruction": "gpt5_model",            # strong
    "witness-analysis": "gpt5_mini_model",          # efficient
    "legal-knowledge": "gpt5_model",                # strong
    "argument-construction": "gpt54_model",         # frontier
    "deliberation": "gpt54_model",                  # frontier
    "governance-verdict": "gpt54_model",            # frontier
}


class TestAgentYamlLoading:
    """Verify all 9 agent YAMLs load without error."""

    @pytest.mark.parametrize("agent_name", AGENT_NAMES)
    def test_yaml_loads(self, agent_name):
        config_path = CONFIGS_DIR / f"{agent_name}.yaml"
        assert config_path.exists(), f"Config missing: {config_path}"

        raw = _load_yaml_with_includes(config_path)
        assert raw is not None

    @pytest.mark.parametrize("agent_name", AGENT_NAMES)
    def test_yaml_has_required_keys(self, agent_name):
        """Each agent YAML should have instruction and either apps or model_tier."""
        config_path = CONFIGS_DIR / f"{agent_name}.yaml"
        raw = _load_yaml_with_includes(config_path)
        has_sam = "apps" in raw
        has_legacy = "model_tier" in raw
        assert has_sam or has_legacy, (
            f"{agent_name}.yaml missing both 'apps' (SAM) and 'model_tier' (legacy)"
        )


class TestParseSamYaml:
    """Test PipelineRunner._parse_sam_yaml adapter."""

    def test_sam_format_extracts_instruction(self):
        raw = {
            "apps": [
                {
                    "app_config": {
                        "instruction": "You are a test agent.",
                        "model": {"model": "gpt-5", "api_key": "sk-test"},
                        "display_name": "Test Agent",
                        "agent_name": "test-agent",
                    }
                }
            ]
        }
        result = PipelineRunner._parse_sam_yaml(raw)
        assert result["instruction"] == "You are a test agent."

    def test_sam_format_extracts_model_name(self):
        raw = {
            "apps": [
                {
                    "app_config": {
                        "instruction": "Test",
                        "model": {"model": "gpt-5.4-nano", "api_key": "sk-test"},
                    }
                }
            ]
        }
        result = PipelineRunner._parse_sam_yaml(raw)
        assert result["model_name"] == "gpt-5.4-nano"

    def test_sam_format_extracts_display_name(self):
        raw = {
            "apps": [
                {
                    "app_config": {
                        "instruction": "Test",
                        "model": {"model": "gpt-5"},
                        "display_name": "My Agent",
                        "agent_name": "my-agent",
                    }
                }
            ]
        }
        result = PipelineRunner._parse_sam_yaml(raw)
        assert result["display_name"] == "My Agent"

    def test_sam_format_model_as_string(self):
        """Handle edge case where model value is a plain string."""
        raw = {
            "apps": [
                {
                    "app_config": {
                        "instruction": "Test",
                        "model": "gpt-5-mini",
                    }
                }
            ]
        }
        result = PipelineRunner._parse_sam_yaml(raw)
        assert result["model_name"] == "gpt-5-mini"

    def test_legacy_format_passthrough(self):
        raw = {
            "name": "test-agent",
            "model_tier": "lightweight",
            "instruction": "You are a legacy agent.",
        }
        result = PipelineRunner._parse_sam_yaml(raw)
        assert result["instruction"] == "You are a legacy agent."
        assert result["model_tier"] == "lightweight"
        # Should not have model_name
        assert "model_name" not in result

    def test_legacy_format_preserves_all_keys(self):
        raw = {
            "name": "test-agent",
            "model_tier": "strong",
            "instruction": "Test",
            "tools": ["parse_document"],
        }
        result = PipelineRunner._parse_sam_yaml(raw)
        assert result == raw


class TestResolveModel:
    """Test _resolve_model with both formats."""

    def test_resolve_model_from_sam_format(self):
        runner = PipelineRunner.__new__(PipelineRunner)
        config = {"model_name": "gpt-5.4-nano", "instruction": "test"}
        assert runner._resolve_model(config) == "gpt-5.4-nano"

    def test_resolve_model_from_legacy_format(self):
        runner = PipelineRunner.__new__(PipelineRunner)
        config = {"model_tier": "lightweight", "instruction": "test"}
        # This will call getattr(settings, "openai_model_lightweight")
        # which returns the actual model name from settings
        model = runner._resolve_model(config)
        assert isinstance(model, str)
        assert len(model) > 0

    def test_resolve_model_sam_takes_precedence(self):
        """If both model_name and model_tier exist, model_name wins."""
        runner = PipelineRunner.__new__(PipelineRunner)
        config = {
            "model_name": "custom-model",
            "model_tier": "lightweight",
            "instruction": "test",
        }
        assert runner._resolve_model(config) == "custom-model"

    def test_resolve_model_invalid_tier_raises(self):
        runner = PipelineRunner.__new__(PipelineRunner)
        config = {"model_tier": "nonexistent", "instruction": "test"}
        with pytest.raises(ValueError, match="Unknown model tier"):
            runner._resolve_model(config)

    def test_resolve_model_unresolved_env_var_falls_back(self):
        """Unresolved ${VAR} in model_name should fall back to settings default."""
        runner = PipelineRunner.__new__(PipelineRunner)
        config = {"model_name": "${OPENAI_MODEL_LIGHTWEIGHT}", "instruction": "test"}
        model = runner._resolve_model(config)
        # Should not contain the placeholder
        assert "${" not in model
        assert isinstance(model, str)
        assert len(model) > 0
