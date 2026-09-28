"""Tests for demo mode functionality in GatewayClient."""

from __future__ import annotations

import pytest

from ai_gateway.config.models import LiteLLMConfig
from ai_gateway.gateway_client import GatewayClient


class TestGatewayClientDemoMode:
    """Test suite for demo mode functionality."""

    def test_demo_mode_config_flag(self):
        """Test that demo_mode can be set via config."""
        config = LiteLLMConfig(demo_mode=True)
        assert config.demo_mode is True

    def test_demo_mode_default_is_false(self):
        """Test that demo_mode defaults to False."""
        config = LiteLLMConfig()
        assert config.demo_mode is False

    def test_is_demo_mode_from_config(self, monkeypatch):
        """Test _is_demo_mode returns True when config has demo_mode=True."""
        monkeypatch.delenv("DEMO_MODE", raising=False)
        config = LiteLLMConfig(demo_mode=True)
        client = GatewayClient(config)
        assert client._is_demo_mode() is True

    def test_is_demo_mode_from_env_var(self, monkeypatch):
        """Test _is_demo_mode returns True when DEMO_MODE env var is set."""
        monkeypatch.setenv("DEMO_MODE", "true")
        config = LiteLLMConfig(demo_mode=False)
        client = GatewayClient(config)
        assert client._is_demo_mode() is True

    def test_is_demo_mode_case_insensitive(self, monkeypatch):
        """Test _is_demo_mode is case insensitive for env var."""
        monkeypatch.setenv("DEMO_MODE", "TRUE")
        config = LiteLLMConfig(demo_mode=False)
        client = GatewayClient(config)
        assert client._is_demo_mode() is True

    def test_is_demo_mode_false_when_disabled(self, monkeypatch):
        """Test _is_demo_mode returns False when disabled."""
        monkeypatch.delenv("DEMO_MODE", raising=False)
        config = LiteLLMConfig(demo_mode=False)
        client = GatewayClient(config)
        assert client._is_demo_mode() is False

    def test_get_demo_models_returns_expected_models(self, monkeypatch):
        """Test _get_demo_models returns a non-empty list."""
        monkeypatch.delenv("DEMO_MODE", raising=False)
        config = LiteLLMConfig(demo_mode=True)
        client = GatewayClient(config)
        models = client._get_demo_models()
        assert isinstance(models, list)
        assert len(models) > 0
        assert "gemini/gemini-2.5-flash" in models

    @pytest.mark.asyncio
    async def test_list_models_returns_demo_models_in_demo_mode(self, monkeypatch):
        """Test list_models returns demo models when in demo mode."""
        monkeypatch.delenv("DEMO_MODE", raising=False)
        config = LiteLLMConfig(demo_mode=True)
        client = GatewayClient(config)
        models = await client.list_models()
        assert isinstance(models, list)
        assert len(models) > 0
        assert "gemini/gemini-2.5-flash" in models

    @pytest.mark.asyncio
    async def test_health_check_returns_true_in_demo_mode(self, monkeypatch):
        """Test health_check returns True in demo mode without calling LiteLLM."""
        monkeypatch.delenv("DEMO_MODE", raising=False)
        config = LiteLLMConfig(demo_mode=True)
        client = GatewayClient(config)
        result = await client.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_get_demo_response_returns_valid_structure(self, monkeypatch):
        """Test _get_demo_response returns a proper response structure with simulated delay."""
        monkeypatch.delenv("DEMO_MODE", raising=False)
        config = LiteLLMConfig(demo_mode=True)
        client = GatewayClient(config)
        
        payload = {"messages": [{"role": "user", "content": "Hello"}]}
        response = await client._get_demo_response("gemini/gemini-2.5-flash", payload)
        
        assert response.success is True
        assert response.status_code == 200
        assert response.body is not None
        assert "choices" in response.body
        assert len(response.body["choices"]) > 0
        assert "message" in response.body["choices"][0]
        assert response.body["choices"][0]["message"]["role"] == "assistant"