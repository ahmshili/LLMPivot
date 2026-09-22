from __future__ import annotations

import time

import pytest

from ai_gateway.admin.testing import (
    build_test_candidate,
    first_concrete_model,
    ordered_test_models,
)
from ai_gateway.admin.testing import test_all_accounts as run_test_all_accounts
from ai_gateway.admin.testing import test_candidate as run_test_candidate
from ai_gateway.admin.testing import test_candidate_with_retry as run_test_candidate_with_retry
from ai_gateway.config.manager import ConfigManager
from ai_gateway.config.models import AccountConfig, AdminConfig, ModelEntry, ProviderConfig, ProviderDefaults
from ai_gateway.failure import FailureType
from ai_gateway.gateway_client import GatewayResponse


class FakeGatewayClient:
    def __init__(self, responses: list[GatewayResponse]) -> None:
        self._responses = list(responses)
        self.calls: list = []

    async def send_chat_completion(self, candidate, payload, *, timeout_seconds=None):
        self.calls.append(candidate)
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_no_key_loaded_short_circuits_without_network_call(
    sample_config_path, monkeypatch
) -> None:
    # Deliberately do NOT set any account env vars.
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    candidate = build_test_candidate(config, manager, "gemini", "personal", "gemini-2.5-flash")

    gateway_client = FakeGatewayClient([])  # would raise IndexError if actually called
    outcome = await run_test_candidate(gateway_client, candidate)

    assert outcome.success is False
    assert outcome.failure_type is None
    assert "No value loaded for environment variable" in outcome.message
    assert outcome.label == "CONFIG_ERROR"
    assert len(gateway_client.calls) == 0


@pytest.mark.asyncio
async def test_successful_call_returns_ok_outcome(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    candidate = build_test_candidate(config, manager, "gemini", "personal", "gemini-2.5-flash")

    gateway_client = FakeGatewayClient(
        [GatewayResponse(success=True, status_code=200, body={"ok": True}, raw_text="{}")]
    )
    outcome = await run_test_candidate(gateway_client, candidate)
    assert outcome.success is True
    assert outcome.label == "OK"


@pytest.mark.asyncio
async def test_retry_recovers_transient_failure(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    candidate = build_test_candidate(config, manager, "gemini", "personal", "gemini-2.5-flash")

    gateway_client = FakeGatewayClient(
        [
            GatewayResponse(success=False, status_code=500, body=None, raw_text="server error"),
            GatewayResponse(success=True, status_code=200, body={"ok": True}, raw_text="{}"),
        ]
    )
    outcome = await run_test_candidate_with_retry(gateway_client, candidate, retry_delay_seconds=0)
    assert outcome.success is True
    assert len(gateway_client.calls) == 2


@pytest.mark.asyncio
async def test_no_retry_for_auth_failure(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    candidate = build_test_candidate(config, manager, "gemini", "personal", "gemini-2.5-flash")

    gateway_client = FakeGatewayClient(
        [GatewayResponse(success=False, status_code=401, body=None, raw_text="unauthorized")]
    )
    outcome = await run_test_candidate_with_retry(gateway_client, candidate, retry_delay_seconds=0)
    assert outcome.success is False
    assert outcome.failure_type == FailureType.AUTH_FAILURE
    assert len(gateway_client.calls) == 1  # no retry attempted


@pytest.mark.asyncio
async def test_no_retry_for_model_not_found(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    candidate = build_test_candidate(config, manager, "gemini", "personal", "gemini-2.5-flash")

    gateway_client = FakeGatewayClient(
        [GatewayResponse(success=False, status_code=404, body=None, raw_text="not found")]
    )
    outcome = await run_test_candidate_with_retry(gateway_client, candidate, retry_delay_seconds=0)
    assert len(gateway_client.calls) == 1


def test_first_concrete_model_expands_wildcard() -> None:
    provider = ProviderConfig(defaults=ProviderDefaults(models=["gemini-2.5-*"]))
    live_models = ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-1.5-flash"]
    assert first_concrete_model(provider, live_models) in ("gemini-2.5-pro", "gemini-2.5-flash")


def test_first_concrete_model_trusts_literal() -> None:
    provider = ProviderConfig(defaults=ProviderDefaults(models=["llama-3.3-70b-versatile"]))
    assert first_concrete_model(provider, []) == "llama-3.3-70b-versatile"


def test_first_concrete_model_handles_model_entry_objects() -> None:
    provider = ProviderConfig(
        defaults=ProviderDefaults(models=[ModelEntry(pattern="gemini-2.5-flash", comment="default")])
    )
    assert first_concrete_model(provider, ["gemini-2.5-flash"]) == "gemini-2.5-flash"


def test_first_concrete_model_falls_back_to_first_live_model() -> None:
    provider = ProviderConfig(defaults=ProviderDefaults(models=[]))
    assert first_concrete_model(provider, ["some-model"]) == "some-model"
    assert first_concrete_model(provider, []) is None


def test_first_concrete_model_skips_unmatched_wildcard_to_next_pattern() -> None:
    provider = ProviderConfig(defaults=ProviderDefaults(models=["nonexistent-*", "real-model"]))
    assert first_concrete_model(provider, ["real-model"]) == "real-model"


def test_ordered_test_models_puts_priority_matches_first_not_alphabetical() -> None:
    # Alphabetically, gemini-2.5-flash would sort before gemini-2.5-pro,
    # but the priority pattern order says pro-family patterns first here.
    provider = ProviderConfig(defaults=ProviderDefaults(models=["gemini-2.5-pro*", "gemini-2.5-flash*"]))
    live_models = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"]
    ordered = ordered_test_models(provider, live_models)
    assert ordered[0] == "gemini-2.5-pro"
    assert set(ordered) == set(live_models)


def test_ordered_test_models_appends_remaining_live_models_after_priority_matches() -> None:
    provider = ProviderConfig(defaults=ProviderDefaults(models=["gemini-2.5-flash"]))
    live_models = ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-1.5-flash"]
    ordered = ordered_test_models(provider, live_models)
    assert ordered[0] == "gemini-2.5-flash"
    assert set(ordered) == set(live_models)
    assert len(ordered) == len(live_models)


def test_ordered_test_models_empty_priority_list_returns_live_models_as_is() -> None:
    provider = ProviderConfig(defaults=ProviderDefaults(models=[]))
    live_models = ["b-model", "a-model"]
    assert ordered_test_models(provider, live_models) == live_models


@pytest.mark.asyncio
async def test_batch_test_respects_sequential_concurrency_and_delay(
    sample_config_path, account_env_vars
) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()

    call_times: list[float] = []

    class TimingGatewayClient:
        async def send_chat_completion(self, candidate, payload, *, timeout_seconds=None):
            call_times.append(time.monotonic())
            return GatewayResponse(success=True, status_code=200, body={"ok": True}, raw_text="{}")

    admin_config = AdminConfig(test_concurrency=1, test_delay_seconds=0.05, test_retry_delay_seconds=0)
    live_models = ["gemini-2.5-flash"]

    results = await run_test_all_accounts(
        TimingGatewayClient(), config, manager, "gemini", live_models, admin_config
    )

    assert len(results) == len(config.providers["gemini"].accounts)
    assert all(r.outcome.success for r in results)
    # Sequential with a delay: calls should be spaced out, not simultaneous.
    if len(call_times) >= 2:
        assert call_times[1] - call_times[0] >= 0.04


@pytest.mark.asyncio
async def test_batch_test_uses_username_for_display(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.providers["gemini"].accounts["personal"] = AccountConfig(username="displayed@example.com")

    class OkGatewayClient:
        async def send_chat_completion(self, candidate, payload, *, timeout_seconds=None):
            return GatewayResponse(success=True, status_code=200, body={"ok": True}, raw_text="{}")

    admin_config = AdminConfig(test_concurrency=5, test_delay_seconds=0, test_retry_delay_seconds=0)
    results = await run_test_all_accounts(
        OkGatewayClient(), config, manager, "gemini", ["gemini-2.5-flash"], admin_config
    )
    usernames = {r.username for r in results}
    assert "displayed@example.com" in usernames
