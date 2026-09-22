from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_gateway.admin.config_writer import ConfigWriter
from ai_gateway.admin.routes import router as admin_router
from ai_gateway.config.manager import ConfigManager
from ai_gateway.cooldown import CooldownManager
from ai_gateway.failure import FailureClassifier
from ai_gateway.router import Router


class StubGatewayClient:
    def __init__(self, models: list[str] | None = None) -> None:
        self._models = models or []

    async def list_models(self):
        return self._models

    async def health_check(self) -> bool:
        return True


def build_admin_app(config_path, models: list[str] | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router)

    config_manager = ConfigManager(config_path)
    config_manager.load()
    gateway_client = StubGatewayClient(models)
    cooldown_manager = CooldownManager(config_manager.config.cooldown)
    gateway_router = Router(
        candidates_by_endpoint={},
        cooldown_manager=cooldown_manager,
        failure_classifier=FailureClassifier(),
        gateway_client=gateway_client,
        routing_config=config_manager.config.routing,
    )

    app.state.config_manager = config_manager
    app.state.config_writer = ConfigWriter(config_manager)
    app.state.gateway_client = gateway_client
    app.state.cooldown_manager = cooldown_manager
    app.state.router = gateway_router
    app.state.env_file_path = config_path.with_name(".env")
    return app


def test_export_page_loads_successfully(sample_config_path, account_env_vars) -> None:
    app = build_admin_app(sample_config_path)
    client = TestClient(app)
    resp = client.get("/admin/export")
    assert resp.status_code == 200


def test_export_includes_provider_names_and_endpoint_names(sample_config_path, account_env_vars) -> None:
    app = build_admin_app(sample_config_path)
    client = TestClient(app)
    resp = client.get("/admin/export")
    body = resp.text
    for provider_name in ["gemini", "groq"]:
        assert provider_name in body


def test_export_never_includes_api_keys(sample_config_path, account_env_vars) -> None:
    """The whole point of this feature is safe-to-paste-anywhere text --
    it must never include actual secret values, only usernames.
    """
    app = build_admin_app(sample_config_path)
    client = TestClient(app)
    resp = client.get("/admin/export")
    body = resp.text
    assert "test-personal-key" not in body
    assert "test-work-key" not in body


def test_export_includes_live_models_from_litellm(sample_config_path, account_env_vars) -> None:
    app = build_admin_app(sample_config_path, models=["gemini/gemini-2.5-flash", "gemini/gemini-2.5-pro"])
    client = TestClient(app)
    resp = client.get("/admin/export")
    body = resp.text
    assert "gemini-2.5-flash" in body
    assert "gemini-2.5-pro" in body


def test_export_reflects_priority_and_standard_model_lists(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.providers["groq"].defaults.models = ["priority-model-x"]
    config.providers["groq"].defaults.standard_models = ["standard-model-y"]
    ConfigWriter(manager).write(config)

    app = build_admin_app(sample_config_path)
    client = TestClient(app)
    resp = client.get("/admin/export")
    body = resp.text
    assert "priority-model-x" in body
    assert "standard-model-y" in body
