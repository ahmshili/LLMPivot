from __future__ import annotations

import pytest

from ai_gateway.config.manager import ConfigManager, default_env_var_name
from ai_gateway.config.models import GatewayConfig


def test_default_env_var_name() -> None:
    assert default_env_var_name("gemini", "personal") == "GEMINI_PERSONAL_API_KEY"
    assert default_env_var_name("open-router", "my-acct") == "OPEN_ROUTER_MY_ACCT_API_KEY"


def test_load_valid_config(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    assert isinstance(config, GatewayConfig)
    assert "gemini" in config.providers
    assert "planner" in config.endpoints


def test_load_missing_file_raises(tmp_path) -> None:
    manager = ConfigManager(tmp_path / "does-not-exist.yaml")
    with pytest.raises(FileNotFoundError):
        manager.load()


def test_endpoint_referencing_unknown_provider_raises(sample_config_dict) -> None:
    bad_config = dict(sample_config_dict)
    bad_config["endpoints"] = {"planner": {"providers": [{"name": "not-a-provider"}]}}
    with pytest.raises(ValueError, match="unknown provider"):
        GatewayConfig.model_validate(bad_config)


def test_endpoint_referencing_unknown_account_raises(sample_config_dict) -> None:
    bad_config = dict(sample_config_dict)
    bad_config["endpoints"] = {
        "planner": {"providers": [{"name": "gemini", "accounts": ["ghost-account"]}]}
    }
    with pytest.raises(ValueError, match="unknown account"):
        GatewayConfig.model_validate(bad_config)


def test_explicit_api_key_env_override() -> None:
    config = GatewayConfig.model_validate(
        {
            "providers": {
                "gemini": {
                    "accounts": {"backup": {"api_key_env": "CUSTOM_ENV_NAME"}},
                }
            },
            "endpoints": {},
        }
    )
    manager = ConfigManager("unused.yaml")
    manager._config = config  # bypass file I/O for this unit test
    assert manager.resolve_api_key_env("gemini", "backup") == "CUSTOM_ENV_NAME"


def test_extra_fields_forbidden() -> None:
    with pytest.raises(Exception):
        GatewayConfig.model_validate({"providers": {}, "endpoints": {}, "unexpected_field": 1})
