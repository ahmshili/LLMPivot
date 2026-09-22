from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from ai_gateway.security import require_admin_auth, require_api_auth


def build_app(*, prod_mode: bool, admin_enabled: bool = False, api_token: str | None = None, admin_token: str | None = None) -> FastAPI:
    app = FastAPI()

    @app.get("/protected-api", dependencies=[Depends(require_api_auth)])
    async def protected_api():
        return {"ok": True}

    @app.get("/open-health")
    async def open_health():
        return {"ok": True}

    @app.get("/protected-admin", dependencies=[Depends(require_admin_auth)])
    async def protected_admin():
        return {"ok": True}

    app.state.prod_mode = prod_mode
    app.state.admin_enabled = admin_enabled
    app.state.api_auth_token = api_token
    app.state.admin_auth_token = admin_token
    return app


# --- require_api_auth ---


def test_api_auth_not_enforced_when_prod_mode_off() -> None:
    app = build_app(prod_mode=False, api_token="secret")
    client = TestClient(app)
    resp = client.get("/protected-api")
    assert resp.status_code == 200


def test_api_auth_not_enforced_when_no_token_configured() -> None:
    """Prod mode on, but no token configured -- an explicit choice to
    leave /v1/* open, not an accidental gap. See SecurityConfig's
    docstring for the reasoning.
    """
    app = build_app(prod_mode=True, api_token=None)
    client = TestClient(app)
    resp = client.get("/protected-api")
    assert resp.status_code == 200


def test_api_auth_rejects_missing_header_when_enforced() -> None:
    app = build_app(prod_mode=True, api_token="secret")
    client = TestClient(app)
    resp = client.get("/protected-api")
    assert resp.status_code == 401


def test_api_auth_rejects_wrong_token() -> None:
    app = build_app(prod_mode=True, api_token="secret")
    client = TestClient(app)
    resp = client.get("/protected-api", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_api_auth_rejects_malformed_header() -> None:
    app = build_app(prod_mode=True, api_token="secret")
    client = TestClient(app)
    resp = client.get("/protected-api", headers={"Authorization": "secret"})
    assert resp.status_code == 401


def test_api_auth_accepts_correct_token() -> None:
    app = build_app(prod_mode=True, api_token="secret")
    client = TestClient(app)
    resp = client.get("/protected-api", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 200


# --- require_admin_auth ---


def test_admin_auth_not_enforced_when_prod_mode_off() -> None:
    """Local/trusted-network usage: admin UI stays exactly as reachable
    and unauthenticated as it's always been.
    """
    app = build_app(prod_mode=False, admin_enabled=False)
    client = TestClient(app)
    resp = client.get("/protected-admin")
    assert resp.status_code == 200


def test_admin_auth_returns_404_when_prod_mode_on_and_not_enabled() -> None:
    """Deliberately 404, not 401/403 -- a public scanner shouldn't be
    able to tell there's an admin UI here at all.
    """
    app = build_app(prod_mode=True, admin_enabled=False)
    client = TestClient(app)
    resp = client.get("/protected-admin")
    assert resp.status_code == 404


def test_admin_auth_rejects_missing_token_when_enabled() -> None:
    app = build_app(prod_mode=True, admin_enabled=True, admin_token="admin-secret")
    client = TestClient(app)
    resp = client.get("/protected-admin")
    assert resp.status_code == 401


def test_admin_auth_rejects_wrong_token_when_enabled() -> None:
    app = build_app(prod_mode=True, admin_enabled=True, admin_token="admin-secret")
    client = TestClient(app)
    resp = client.get("/protected-admin", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_admin_auth_accepts_correct_token_when_enabled() -> None:
    app = build_app(prod_mode=True, admin_enabled=True, admin_token="admin-secret")
    client = TestClient(app)
    resp = client.get("/protected-admin", headers={"Authorization": "Bearer admin-secret"})
    assert resp.status_code == 200


def test_admin_auth_rejects_even_with_correct_api_token() -> None:
    """The two secrets are deliberately separate -- the API token must
    never grant admin access.
    """
    app = build_app(prod_mode=True, admin_enabled=True, api_token="api-secret", admin_token="admin-secret")
    client = TestClient(app)
    resp = client.get("/protected-admin", headers={"Authorization": "Bearer api-secret"})
    assert resp.status_code == 401


def test_health_route_has_no_api_auth_dependency() -> None:
    """Regression guard: /health must stay reachable without
    authentication even in prod mode, for PaaS health checks. If a
    future change accidentally adds require_api_auth to it (e.g. by
    switching api_router's inclusion back to a router-wide dependency),
    this catches it.
    """
    from ai_gateway.api import router as api_router

    health_route = next(r for r in api_router.routes if r.path == "/health")
    dependency_funcs = [dep.dependency for dep in health_route.dependencies]
    assert require_api_auth not in dependency_funcs


def test_chat_completions_and_models_and_reload_have_api_auth_dependency() -> None:
    """Companion regression guard: the three routes that SHOULD require
    the API token actually do.
    """
    from ai_gateway.api import router as api_router

    protected_paths = {"/v1/chat/completions", "/v1/models", "/internal/reload"}
    for route in api_router.routes:
        if route.path in protected_paths:
            dependency_funcs = [dep.dependency for dep in route.dependencies]
            assert require_api_auth in dependency_funcs, f"{route.path} is missing require_api_auth"
