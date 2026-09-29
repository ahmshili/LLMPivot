from __future__ import annotations

import time

import pytest

from ai_gateway.admin.testing import (
    build_test_candidate,
    extract_provider_error_message,
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


@pytest.mark.asyncio
async def test_retry_logs_ok_outcome_matching_ui(
    sample_config_path, account_env_vars, caplog
) -> None:
    """The app log must state what the UI fragment shows: an OK badge for
    a passing test. (The HTTP access-log line alone only proves a
    fragment was rendered -- it always says 200 even when the provider
    call failed.)
    """
    import logging

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    candidate = build_test_candidate(config, manager, "gemini", "personal", "gemini-2.5-flash")

    gateway_client = FakeGatewayClient(
        [GatewayResponse(success=True, status_code=200, body={"ok": True}, raw_text="{}")]
    )
    with caplog.at_level(logging.INFO, logger="ai_gateway.admin.testing"):
        outcome = await run_test_candidate_with_retry(gateway_client, candidate, retry_delay_seconds=0)

    assert outcome.success is True
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any(
        "[gemini/personal] Test OK -- model 'gemini-2.5-flash'" in r.getMessage() for r in infos
    )
    # ...and nothing may claim a failure for a passing test.
    assert not any("Test FAILED" in r.getMessage() for r in infos)


@pytest.mark.asyncio
async def test_retry_logs_failure_outcome_matching_ui(
    sample_config_path, account_env_vars, caplog
) -> None:
    """Regression test for the reported symptom: the UI showed
    AUTH_FAILURE (403) while the log only showed the admin route's own
    '200 OK' access line, with no record of the provider-level failure.
    The outcome log line must carry the same badge + status the UI shows.
    """
    import logging

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    candidate = build_test_candidate(config, manager, "gemini", "work", "gemini-3.6-flash")

    gateway_client = FakeGatewayClient(
        [
            GatewayResponse(
                success=False,
                status_code=403,
                body=None,
                raw_text='{"error":{"code":403,"message":"Your project has been denied access.","status":"PERMISSION_DENIED"}}',
            )
        ]
    )
    with caplog.at_level(logging.INFO, logger="ai_gateway.admin.testing"):
        outcome = await run_test_candidate_with_retry(gateway_client, candidate, retry_delay_seconds=0)

    assert outcome.success is False
    assert outcome.failure_type == FailureType.AUTH_FAILURE
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    outcome_lines = [r.getMessage() for r in infos if "Test FAILED" in r.getMessage()]
    assert outcome_lines, "failure outcome was never logged"
    line = outcome_lines[0]
    # Account tag FIRST at column 0, then verdict, then model details.
    assert line.startswith("[gemini/work] Test FAILED")
    assert "-- model 'gemini-3.6-flash': AUTH_FAILURE (403)" in line
    assert "AUTH_FAILURE (403)" in line
    # Deterministic failures are not retried -- exactly one outcome line.
    assert len(outcome_lines) == 1


def test_short_message_collapses_multiline_bodies() -> None:
    from ai_gateway.admin.testing import _short_message

    messy = '{\n  "error": {\n    "message": "boom",\n    "status": "PERMISSION_DENIED"\n  }\n}'
    collapsed = _short_message(messy)
    assert "\n" not in collapsed
    assert "boom" in collapsed
    assert _short_message("x" * 500).endswith("...")
    assert len(_short_message("x" * 500)) == 200


# -- extract_provider_error_message ------------------------------------------

# The exact shape from a real incident log: LiteLLM relaying Gemini's 403
# PERMISSION_DENIED, with the provider's own JSON embedded INSIDE
# error.message as an escaped string.
_GEMINI_403_VIA_LITELLM = (
    '{"error":{"message":"litellm.BadRequestError: GeminiException BadRequestError - {'
    '\\n \\"error\\": {\\n \\"code\\": 403,\\n \\"message\\": \\"Your project has been '
    'denied access. Please contact support.\\",\\n \\"status\\": \\"PERMISSION_DENIED\\"'
    '\\n }\\n}"}}'
)


def test_extract_unwraps_litellm_wrapper_to_provider_message() -> None:
    assert (
        extract_provider_error_message(_GEMINI_403_VIA_LITELLM)
        == "Your project has been denied access. Please contact support."
    )


def test_extract_handles_simple_error_object() -> None:
    body = '{"error":{"message":"model not found","code":404}}'
    assert extract_provider_error_message(body) == "model not found"


def test_extract_handles_bare_message_object() -> None:
    assert extract_provider_error_message('{"message":"quota exceeded"}') == "quota exceeded"


def test_extract_handles_embedded_json_in_non_json_text() -> None:
    text = 'upstream said: {"error":{"message":"invalid key"}} (end)'
    assert extract_provider_error_message(text) == "invalid key"


def test_extract_keeps_non_json_text_as_is() -> None:
    # A non-JSON failure body is still a perfectly readable message.
    assert extract_provider_error_message("plain gateway timeout") == "plain gateway timeout"
    assert extract_provider_error_message("") is None


@pytest.mark.asyncio
async def test_failure_message_is_friendly_not_raw_blob(
    sample_config_path, account_env_vars, caplog
) -> None:
    """The exact 403 body from the incident log must surface the upstream
    reason in outcome.message (shown in the UI fragment and the log line)
    instead of the truncated nested-JSON wrapper. Full body at DEBUG."""
    import logging

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    candidate = build_test_candidate(config, manager, "gemini", "work", "gemini-3.6-flash")

    gateway_client = FakeGatewayClient(
        [GatewayResponse(success=False, status_code=403, body=None, raw_text=_GEMINI_403_VIA_LITELLM)]
    )
    with caplog.at_level(logging.DEBUG, logger="ai_gateway.admin.testing"):
        outcome = await run_test_candidate(gateway_client, candidate)

    assert outcome.success is False
    assert outcome.message == "Your project has been denied access. Please contact support."
    # The full body stays available at DEBUG for post-mortems.
    assert any(r.levelno == logging.DEBUG and "PERMISSION_DENIED" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_retry_line_leads_with_account_model_tag(
    sample_config_path, account_env_vars, caplog
) -> None:
    """The retry announcement must lead with the [provider/account/model]
    tag like the outcome lines, so a scan of the log reads identities at
    column 0 consistently.
    """
    import logging

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    candidate = build_test_candidate(config, manager, "gemini", "personal", "gemini-2.5-flash")

    gateway_client = FakeGatewayClient(
        [
            GatewayResponse(success=False, status_code=500, body=None, raw_text="server error"),
            GatewayResponse(success=True, status_code=200, body={}, raw_text="{}"),
        ]
    )
    with caplog.at_level(logging.INFO, logger="ai_gateway.admin.testing"):
        await run_test_candidate_with_retry(gateway_client, candidate, retry_delay_seconds=0)

    retry_lines = [r.getMessage() for r in caplog.records if "retrying once" in r.getMessage()]
    assert retry_lines, "retry announcement was never logged"
    assert retry_lines[0].startswith("[gemini/personal/gemini-2.5-flash] Test failed with TRANSIENT")
