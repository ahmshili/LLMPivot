from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient

from ai_gateway.admin.config_writer import ConfigWriter
from ai_gateway.admin.routes import router as admin_router
from ai_gateway.config.manager import ConfigManager
from ai_gateway.cooldown import CooldownManager
from ai_gateway.failure import FailureClassifier
from ai_gateway.landing import router as landing_router
from ai_gateway.router import Router
from ai_gateway.security import (
    DEMO_READ_ONLY_MESSAGE,
    DemoReadOnlyError,
    enforce_demo_read_only,
)


class StubGatewayClient:
    async def list_models(self):
        return []

    async def health_check(self) -> bool:
        return True


def build_app(config_path, *, demo_mode: bool, admin_available: bool = True) -> FastAPI:
    """Mirror main.py's wiring (router dependencies + exception handler)
    without running the real lifespan."""
    app = FastAPI()

    @app.exception_handler(DemoReadOnlyError)
    async def _handler(request: Request, exc: DemoReadOnlyError):
        if "text/html" in request.headers.get("accept", ""):
            return HTMLResponse(DEMO_READ_ONLY_MESSAGE, status_code=403)
        return JSONResponse({"detail": DEMO_READ_ONLY_MESSAGE}, status_code=403)

    app.include_router(landing_router)
    app.include_router(admin_router, dependencies=[Depends(enforce_demo_read_only)])

    config_manager = ConfigManager(config_path)
    config_manager.load()
    gateway_client = StubGatewayClient()
    cooldown_manager = CooldownManager(config_manager.config.cooldown)
    app.state.config_manager = config_manager
    app.state.config_writer = ConfigWriter(config_manager)
    app.state.gateway_client = gateway_client
    app.state.cooldown_manager = cooldown_manager
    app.state.router = Router(
        candidates_by_endpoint={},
        cooldown_manager=cooldown_manager,
        failure_classifier=FailureClassifier(),
        gateway_client=gateway_client,
        routing_config=config_manager.config.routing,
    )
    app.state.env_file_path = config_path.with_name(".env")
    app.state.demo_mode = demo_mode
    app.state.admin_available = admin_available
    return app


@pytest.fixture
def demo_client(sample_config_path, account_env_vars) -> TestClient:
    return TestClient(build_app(sample_config_path, demo_mode=True), follow_redirects=False)


@pytest.fixture
def normal_client(sample_config_path, account_env_vars) -> TestClient:
    return TestClient(build_app(sample_config_path, demo_mode=False), follow_redirects=False)


def test_demo_mode_allows_reads_and_shows_banner(demo_client: TestClient) -> None:
    response = demo_client.get("/admin/")
    assert response.status_code == 200
    assert "Read-only public demo" in response.text
    assert demo_client.get("/admin/providers").status_code == 200
    assert demo_client.get("/admin/endpoints").status_code == 200
    assert demo_client.get("/admin/export").status_code == 200


def test_normal_mode_has_no_banner(normal_client: TestClient) -> None:
    assert "Read-only public demo" not in normal_client.get("/admin/").text


@pytest.mark.parametrize(
    "path",
    [
        "/admin/backup",
        "/admin/providers",
        "/admin/providers/gemini",
        "/admin/providers/gemini/delete",
        "/admin/providers/gemini/accounts",
        "/admin/providers/gemini/accounts/personal/delete",
        "/admin/providers/gemini/accounts/reorder",
        "/admin/endpoints",
        "/admin/endpoints/planner",
        "/admin/endpoints/planner/delete",
    ],
)
def test_demo_mode_blocks_every_write(demo_client: TestClient, sample_config_path, path: str) -> None:
    before = sample_config_path.read_text(encoding="utf-8")
    response = demo_client.post(path, data={"name": "x"})
    assert response.status_code == 403
    assert sample_config_path.read_text(encoding="utf-8") == before


def test_demo_mode_write_gets_html_for_browsers_and_json_for_fetch(demo_client: TestClient) -> None:
    html = demo_client.post("/admin/providers/gemini/delete", headers={"accept": "text/html"})
    assert html.status_code == 403
    assert "text/html" in html.headers["content-type"]

    fetch = demo_client.post("/admin/providers/gemini/delete", headers={"accept": "*/*"})
    assert fetch.status_code == 403
    assert fetch.json()["detail"] == DEMO_READ_ONLY_MESSAGE


def test_demo_mode_still_allows_test_buttons(demo_client: TestClient) -> None:
    """Empty model short-circuits before any gateway call, so this only
    proves the guard lets the route through."""
    single = demo_client.post("/admin/providers/gemini/accounts/personal/test", data={"model": ""})
    assert single.status_code == 200


def test_demo_mode_does_not_confuse_a_provider_named_test_with_the_test_route(
    demo_client: TestClient,
) -> None:
    assert demo_client.post("/admin/providers/test", data={"litellm_prefix": "x/"}).status_code == 403
    assert demo_client.post("/admin/providers/gemini/accounts/test/delete").status_code == 403


def test_normal_mode_writes_are_not_blocked(normal_client: TestClient) -> None:
    response = normal_client.post("/admin/providers/gemini/delete")
    assert response.status_code != 403


def test_root_redirects_to_admin_when_available(sample_config_path, account_env_vars) -> None:
    client = TestClient(build_app(sample_config_path, demo_mode=True), follow_redirects=False)
    response = client.get("/")
    assert response.status_code == 302
    assert response.headers["location"] == "/admin/"
    assert client.head("/").status_code == 302


def test_root_returns_json_pointer_when_admin_hidden(sample_config_path, account_env_vars) -> None:
    client = TestClient(
        build_app(sample_config_path, demo_mode=False, admin_available=False), follow_redirects=False
    )
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["docs"] == "/docs"
