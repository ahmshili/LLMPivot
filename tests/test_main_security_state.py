from __future__ import annotations

import pytest

from ai_gateway.config.models import SecurityConfig
from ai_gateway.main import StartupConfigError, _resolve_security_state


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("AI_GATEWAY_PROD_MODE", "AI_GATEWAY_ENABLE_ADMIN", "MY_API_TOKEN", "MY_ADMIN_TOKEN"):
        monkeypatch.delenv(var, raising=False)


def test_defaults_are_all_off(monkeypatch) -> None:
    prod_mode, admin_enabled, api_token, admin_token = _resolve_security_state(SecurityConfig())
    assert prod_mode is False
    assert admin_enabled is False
    assert api_token is None
    assert admin_token is None


def test_prod_mode_without_enable_admin_leaves_admin_disabled(monkeypatch) -> None:
    monkeypatch.setenv("AI_GATEWAY_PROD_MODE", "true")
    prod_mode, admin_enabled, _, _ = _resolve_security_state(SecurityConfig())
    assert prod_mode is True
    assert admin_enabled is False


def test_prod_mode_without_configured_api_token_leaves_api_open(monkeypatch) -> None:
    """No hard failure -- an explicit choice, not an accident."""
    monkeypatch.setenv("AI_GATEWAY_PROD_MODE", "true")
    prod_mode, _, api_token, _ = _resolve_security_state(SecurityConfig())
    assert prod_mode is True
    assert api_token is None


def test_prod_mode_resolves_configured_api_token(monkeypatch) -> None:
    monkeypatch.setenv("AI_GATEWAY_PROD_MODE", "true")
    monkeypatch.setenv("MY_API_TOKEN", "secret-value")
    _, _, api_token, _ = _resolve_security_state(SecurityConfig(api_auth_token_env="MY_API_TOKEN"))
    assert api_token == "secret-value"


def test_enable_admin_without_prod_mode_does_not_enable_admin(monkeypatch) -> None:
    """--enable-admin only means anything alongside --prod."""
    monkeypatch.setenv("AI_GATEWAY_ENABLE_ADMIN", "true")
    prod_mode, admin_enabled, _, _ = _resolve_security_state(SecurityConfig())
    assert prod_mode is False
    assert admin_enabled is False


def test_enable_admin_without_configured_token_raises(monkeypatch) -> None:
    """The critical safety guard: refuse to start rather than expose the
    admin UI (API keys, full config control) with no authentication.
    """
    monkeypatch.setenv("AI_GATEWAY_PROD_MODE", "true")
    monkeypatch.setenv("AI_GATEWAY_ENABLE_ADMIN", "true")
    with pytest.raises(StartupConfigError):
        _resolve_security_state(SecurityConfig())


def test_enable_admin_with_configured_but_unset_env_var_raises(monkeypatch) -> None:
    """admin_auth_token_env names a real env var, but that env var itself
    was never actually set (or is empty) -- same failure mode as not
    configuring it at all, must still raise.
    """
    monkeypatch.setenv("AI_GATEWAY_PROD_MODE", "true")
    monkeypatch.setenv("AI_GATEWAY_ENABLE_ADMIN", "true")
    with pytest.raises(StartupConfigError):
        _resolve_security_state(SecurityConfig(admin_auth_token_env="MY_ADMIN_TOKEN"))


def test_enable_admin_with_valid_token_succeeds(monkeypatch) -> None:
    monkeypatch.setenv("AI_GATEWAY_PROD_MODE", "true")
    monkeypatch.setenv("AI_GATEWAY_ENABLE_ADMIN", "true")
    monkeypatch.setenv("MY_ADMIN_TOKEN", "admin-secret-value")
    prod_mode, admin_enabled, _, admin_token = _resolve_security_state(
        SecurityConfig(admin_auth_token_env="MY_ADMIN_TOKEN")
    )
    assert prod_mode is True
    assert admin_enabled is True
    assert admin_token == "admin-secret-value"


def test_falsy_flag_values_do_not_enable_prod_mode(monkeypatch) -> None:
    monkeypatch.setenv("AI_GATEWAY_PROD_MODE", "false")
    prod_mode, _, _, _ = _resolve_security_state(SecurityConfig())
    assert prod_mode is False
