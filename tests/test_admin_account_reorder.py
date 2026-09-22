from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_gateway.admin.config_writer import ConfigWriter
from ai_gateway.admin.routes import router as admin_router
from ai_gateway.candidates import Candidate
from ai_gateway.config.manager import ConfigManager
from ai_gateway.cooldown import CooldownManager
from ai_gateway.failure import FailureClassifier
from ai_gateway.gateway_client import GatewayResponse
from ai_gateway.router import Router


class StubGatewayClient:
    """Enough of GatewayClient's surface for _safe_reload() to succeed
    without touching the network -- reorder is expected to trigger a
    reload_candidates() call same as every other admin write.
    """

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


def test_reorder_accounts_applies_requested_order(admin_client: TestClient, sample_config_path) -> None:
    # sample_config fixture gives gemini accounts in order: personal, work
    resp = admin_client.post(
        "/admin/providers/gemini/accounts/reorder",
        data={"order": json.dumps(["work", "personal"])},
    )
    assert resp.status_code in (200, 303)

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    assert list(config.providers["gemini"].accounts.keys()) == ["work", "personal"]


def test_reorder_accounts_drops_unknown_keys(admin_client: TestClient, sample_config_path) -> None:
    resp = admin_client.post(
        "/admin/providers/gemini/accounts/reorder",
        data={"order": json.dumps(["work", "not-a-real-account", "personal"])},
    )
    assert resp.status_code in (200, 303)

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    assert list(config.providers["gemini"].accounts.keys()) == ["work", "personal"]


def test_reorder_accounts_appends_omitted_existing_keys(admin_client: TestClient, sample_config_path) -> None:
    # A partial/stale payload (e.g. a race with a concurrent edit) must
    # never silently drop an account that actually exists -- it should
    # just fall back to the end in its prior relative order.
    resp = admin_client.post(
        "/admin/providers/gemini/accounts/reorder",
        data={"order": json.dumps(["work"])},
    )
    assert resp.status_code in (200, 303)

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    assert list(config.providers["gemini"].accounts.keys()) == ["work", "personal"]


def test_reorder_accounts_unknown_provider_is_noop(admin_client: TestClient, sample_config_path) -> None:
    resp = admin_client.post(
        "/admin/providers/does-not-exist/accounts/reorder",
        data={"order": json.dumps(["a", "b"])},
    )
    assert resp.status_code in (200, 303)

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    # Untouched -- no provider named "does-not-exist" exists to mutate.
    assert list(config.providers["gemini"].accounts.keys()) == ["personal", "work"]


def test_reorder_accounts_malformed_json_is_noop(admin_client: TestClient, sample_config_path) -> None:
    resp = admin_client.post(
        "/admin/providers/gemini/accounts/reorder",
        data={"order": "not-json"},
    )
    assert resp.status_code in (200, 303)

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    assert list(config.providers["gemini"].accounts.keys()) == ["personal", "work"]


def test_delete_accounts_bulk_removes_requested_accounts(admin_client: TestClient, sample_config_path) -> None:
    resp = admin_client.post(
        "/admin/providers/gemini/accounts/delete-bulk",
        data={"keys": json.dumps(["personal", "work"])},
    )
    assert resp.status_code in (200, 303)

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    assert config.providers["gemini"].accounts == {}


def test_delete_accounts_bulk_removes_env_lines(admin_client: TestClient, sample_config_path) -> None:
    env_path = sample_config_path.with_name(".env")
    env_path.write_text("GEMINI_PERSONAL_API_KEY=test-personal-key\nGEMINI_WORK_API_KEY=test-work-key\n")

    resp = admin_client.post(
        "/admin/providers/gemini/accounts/delete-bulk",
        data={"keys": json.dumps(["personal", "work"])},
    )
    assert resp.status_code in (200, 303)

    remaining = env_path.read_text()
    assert "GEMINI_PERSONAL_API_KEY" not in remaining
    assert "GEMINI_WORK_API_KEY" not in remaining


def test_delete_accounts_bulk_partial_selection_keeps_the_rest(admin_client: TestClient, sample_config_path) -> None:
    resp = admin_client.post(
        "/admin/providers/gemini/accounts/delete-bulk",
        data={"keys": json.dumps(["personal"])},
    )
    assert resp.status_code in (200, 303)

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    assert list(config.providers["gemini"].accounts.keys()) == ["work"]


def test_delete_accounts_bulk_drops_unknown_keys(admin_client: TestClient, sample_config_path) -> None:
    resp = admin_client.post(
        "/admin/providers/gemini/accounts/delete-bulk",
        data={"keys": json.dumps(["personal", "not-a-real-account"])},
    )
    assert resp.status_code in (200, 303)

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    assert list(config.providers["gemini"].accounts.keys()) == ["work"]


def test_delete_accounts_bulk_unknown_provider_is_noop(admin_client: TestClient, sample_config_path) -> None:
    resp = admin_client.post(
        "/admin/providers/does-not-exist/accounts/delete-bulk",
        data={"keys": json.dumps(["a"])},
    )
    assert resp.status_code in (200, 303)

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    assert list(config.providers["gemini"].accounts.keys()) == ["personal", "work"]


def test_delete_accounts_bulk_malformed_json_is_noop(admin_client: TestClient, sample_config_path) -> None:
    resp = admin_client.post(
        "/admin/providers/gemini/accounts/delete-bulk",
        data={"keys": "not-json"},
    )
    assert resp.status_code in (200, 303)

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    assert list(config.providers["gemini"].accounts.keys()) == ["personal", "work"]


def test_delete_accounts_bulk_empty_selection_is_noop(admin_client: TestClient, sample_config_path) -> None:
    resp = admin_client.post(
        "/admin/providers/gemini/accounts/delete-bulk",
        data={"keys": json.dumps([])},
    )
    assert resp.status_code in (200, 303)

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    assert list(config.providers["gemini"].accounts.keys()) == ["personal", "work"]
