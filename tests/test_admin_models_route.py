from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_gateway.admin.config_writer import ConfigWriter
from ai_gateway.admin.routes import router as admin_router
from ai_gateway.config.manager import ConfigManager
from ai_gateway.config.models import ModelEntry
from ai_gateway.cooldown import CooldownManager
from ai_gateway.failure import FailureClassifier
from ai_gateway.router import Router


class StubGatewayClient:
    async def list_models(self):
        return []

    async def health_check(self) -> bool:
        return True


def build_admin_app(config_path) -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router)

    config_manager = ConfigManager(config_path)
    config_manager.load()
    gateway_client = StubGatewayClient()
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


@pytest.fixture
def admin_client(sample_config_path, account_env_vars) -> TestClient:
    app = build_admin_app(sample_config_path)
    return TestClient(app)


def test_save_models_disabled_entry_persists_as_model_entry(admin_client: TestClient, sample_config_path) -> None:
    payload = [
        {"pattern": "llama-4-scout", "comment": "", "enabled": True},
        {"pattern": "llama-3.3-70b-versatile", "comment": "", "enabled": False},
    ]
    resp = admin_client.post(
        "/admin/providers/groq/models",
        data={"models_json": json.dumps(payload), "exclude_models": ""},
    )
    assert resp.status_code in (200, 303)

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    models = config.providers["groq"].defaults.models
    assert models[0] == "llama-4-scout"  # enabled, no comment -> stays a bare string
    assert isinstance(models[1], ModelEntry)
    assert models[1].pattern == "llama-3.3-70b-versatile"
    assert models[1].enabled is False


def test_save_models_enabled_entry_with_no_comment_serializes_as_bare_string(
    admin_client: TestClient, sample_config_path
) -> None:
    payload = [{"pattern": "llama-4-scout", "comment": "", "enabled": True}]
    resp = admin_client.post(
        "/admin/providers/groq/models",
        data={"models_json": json.dumps(payload), "exclude_models": ""},
    )
    assert resp.status_code in (200, 303)

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    models = config.providers["groq"].defaults.models
    assert models == ["llama-4-scout"]

    # And the written YAML itself shouldn't carry a needless "enabled: true"
    # anywhere -- exclude_defaults=True should have kept it out entirely.
    raw_yaml = sample_config_path.read_text()
    assert "enabled" not in raw_yaml


def test_save_models_disabled_entry_appears_in_written_yaml(admin_client: TestClient, sample_config_path) -> None:
    payload = [{"pattern": "llama-3.3-70b-versatile", "comment": "", "enabled": False}]
    resp = admin_client.post(
        "/admin/providers/groq/models",
        data={"models_json": json.dumps(payload), "exclude_models": ""},
    )
    assert resp.status_code in (200, 303)

    raw_yaml = sample_config_path.read_text()
    assert "enabled: false" in raw_yaml


def test_save_models_missing_enabled_key_defaults_to_true(admin_client: TestClient, sample_config_path) -> None:
    """Older saved payloads (or a browser without JS updated yet) might
    omit `enabled` entirely -- must default to enabled, not silently
    disable everything.
    """
    payload = [{"pattern": "llama-4-scout", "comment": "kept"}]
    resp = admin_client.post(
        "/admin/providers/groq/models",
        data={"models_json": json.dumps(payload), "exclude_models": ""},
    )
    assert resp.status_code in (200, 303)

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    models = config.providers["groq"].defaults.models
    assert isinstance(models[0], ModelEntry)
    assert models[0].enabled is True


def test_save_models_persists_standard_models(admin_client: TestClient, sample_config_path) -> None:
    priority_payload = [{"pattern": "llama-4-scout", "comment": "", "enabled": True}]
    standard_payload = [{"pattern": "llama-3.1-8b-instant", "comment": "backup", "enabled": True}]
    resp = admin_client.post(
        "/admin/providers/groq/models",
        data={
            "models_json": json.dumps(priority_payload),
            "standard_models_json": json.dumps(standard_payload),
            "exclude_models": "",
        },
    )
    assert resp.status_code in (200, 303)

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    defaults = config.providers["groq"].defaults
    assert defaults.models == ["llama-4-scout"]
    assert len(defaults.standard_models) == 1
    assert defaults.standard_models[0].pattern == "llama-3.1-8b-instant"
    assert defaults.standard_models[0].comment == "backup"


def test_save_models_priority_wins_when_pattern_in_both_lists(admin_client: TestClient, sample_config_path) -> None:
    """Mutual exclusivity enforced server-side, not just trusted from the
    UI's own move-button logic -- defense in depth.
    """
    priority_payload = [{"pattern": "llama-4-scout", "comment": "", "enabled": True}]
    standard_payload = [{"pattern": "llama-4-scout", "comment": "", "enabled": True}]
    resp = admin_client.post(
        "/admin/providers/groq/models",
        data={
            "models_json": json.dumps(priority_payload),
            "standard_models_json": json.dumps(standard_payload),
            "exclude_models": "",
        },
    )
    assert resp.status_code in (200, 303)

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    defaults = config.providers["groq"].defaults
    assert defaults.models == ["llama-4-scout"]
    assert defaults.standard_models == []


def test_save_models_standard_list_excluded_from_serialization_when_empty(
    admin_client: TestClient, sample_config_path
) -> None:
    resp = admin_client.post(
        "/admin/providers/groq/models",
        data={
            "models_json": json.dumps([{"pattern": "llama-4-scout", "comment": "", "enabled": True}]),
            "standard_models_json": json.dumps([]),
            "exclude_models": "",
        },
    )
    assert resp.status_code in (200, 303)

    raw_yaml = sample_config_path.read_text()
    assert "standard_models" not in raw_yaml
