"""Ad-hoc account/model test calls for the admin UI.

Deliberately bypasses CooldownManager entirely: a manual test is an
operator action, not a routing decision, and must never mark an account
healthy or disabled based on a single throwaway call. It reuses
GatewayClient and FailureClassifier directly -- the same code path real
traffic uses -- so a passing test means what an operator would expect.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
from dataclasses import dataclass

from ai_gateway.candidates import Candidate, build_litellm_model, pattern_text
from ai_gateway.config.manager import ConfigManager
from ai_gateway.config.models import AdminConfig, GatewayConfig, ProviderConfig
from ai_gateway.failure import FailureClassifier, FailureType
from ai_gateway.gateway_client import GatewayClient

logger = logging.getLogger("ai_gateway.admin.testing")

_TEST_PAYLOAD = {
    "messages": [{"role": "user", "content": "Say OK and nothing else."}],
    "max_tokens": 5,
}

# Failure types worth one automatic retry: genuinely transient signals
# where a second attempt might behave differently. AUTH_FAILURE,
# MODEL_NOT_FOUND, and QUOTA_EXHAUSTED are all deterministic -- retrying
# them just burns another call for the same answer.
_RETRYABLE_FAILURE_TYPES = {FailureType.TRANSIENT, FailureType.NETWORK_FAILURE}


@dataclass
class TestOutcome:
    success: bool
    status_code: int | None
    failure_type: FailureType | None
    message: str

    @property
    def label(self) -> str:
        """Short badge text. Distinct from failure_type so config-level
        problems (no key loaded) can show a clear label without forcing a
        FailureType that doesn't really apply -- FailureType only
        classifies HTTP-layer outcomes.
        """
        if self.success:
            return "OK"
        return self.failure_type.value if self.failure_type else "CONFIG_ERROR"


def build_test_candidate(
    config: GatewayConfig, config_manager: ConfigManager, provider_name: str, account_name: str, model: str
) -> Candidate:
    provider_config = config.providers[provider_name]
    litellm_prefix = provider_config.litellm_prefix or f"{provider_name}/"
    api_key_env = config_manager.resolve_api_key_env(provider_name, account_name)
    account_config = provider_config.accounts.get(account_name)
    api_base = account_config.api_base if account_config else None
    return Candidate(
        endpoint="__admin_test__",
        provider=provider_name,
        model=model,
        account=account_name,
        litellm_model=build_litellm_model(litellm_prefix, model),
        api_key_env=api_key_env,
        api_base=api_base,
    )


async def test_candidate(gateway_client: GatewayClient, candidate: Candidate) -> TestOutcome:
    """Single test attempt, no retry. Short-circuits before any network
    call if the account's env var has no value loaded in this process --
    this is a config problem (wrong AI_GATEWAY_ENV_FILE, wrong launch
    --env-file, account added but process not reloaded), not a network
    failure, and showing it as NETWORK_FAILURE would hide the real cause.
    """
    if not os.environ.get(candidate.api_key_env):
        return TestOutcome(
            success=False,
            status_code=None,
            failure_type=None,
            message=(
                f"No value loaded for environment variable '{candidate.api_key_env}' in this "
                "process. Check that AI_GATEWAY_ENV_FILE and your launch --env-file point at "
                "the same .env file, and that the process has been restarted or reloaded since "
                "the key was added."
            ),
        )

    response = await gateway_client.send_chat_completion(candidate, dict(_TEST_PAYLOAD), timeout_seconds=15)
    if response.success:
        return TestOutcome(success=True, status_code=response.status_code, failure_type=None, message="OK")

    failure_type = FailureClassifier().classify(
        status_code=response.status_code,
        body_text=response.raw_text,
        exception=response.exception,
    )
    if response.raw_text:
        message = response.raw_text[:300]
    elif response.exception:
        message = str(response.exception)
    else:
        message = "Unknown error"
    return TestOutcome(success=False, status_code=response.status_code, failure_type=failure_type, message=message)


async def test_candidate_with_retry(
    gateway_client: GatewayClient, candidate: Candidate, retry_delay_seconds: float = 1.0
) -> TestOutcome:
    """Runs test_candidate, and if it failed with a retryable failure type,
    waits retry_delay_seconds and tries exactly once more.
    """
    outcome = await test_candidate(gateway_client, candidate)
    if not outcome.success and outcome.failure_type in _RETRYABLE_FAILURE_TYPES:
        logger.info(
            "Test for %s/%s/%s failed with %s; retrying once after %.1fs.",
            candidate.provider,
            candidate.account,
            candidate.model,
            outcome.failure_type.value,
            retry_delay_seconds,
        )
        if retry_delay_seconds:
            await asyncio.sleep(retry_delay_seconds)
        outcome = await test_candidate(gateway_client, candidate)
    return outcome


def priority_ordered_models(provider: ProviderConfig, live_models: list[str]) -> list[str]:
    """Just the provider's priority list, resolved to real live model
    names, in priority order. Wildcards expand only to matches in
    live_models; literal patterns are trusted as-is even if not currently
    live (consistent with routing's own trust-the-literal behavior).

    This is deliberately NOT the full live catalog -- it's what the
    endpoint form pre-fills the model picker with when a priority pattern
    like 'gemini-2.5-*' would otherwise leave an unexpanded, unusable
    string sitting in the list.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for raw_entry in provider.defaults.models:
        pattern = pattern_text(raw_entry)
        if any(ch in pattern for ch in "*?["):
            for candidate_model in live_models:
                if fnmatch.fnmatch(candidate_model, pattern) and candidate_model not in seen:
                    seen.add(candidate_model)
                    ordered.append(candidate_model)
        elif pattern not in seen:
            seen.add(pattern)
            ordered.append(pattern)
    return ordered


def ordered_test_models(provider: ProviderConfig, live_models: list[str]) -> list[str]:
    """Models worth offering/using for a Test action, priority-list matches
    first (in priority order), followed by any remaining live models not
    already included. This is what both the Test dropdown and "test all
    accounts" should use -- picking the alphabetically-first live model by
    default ignored the whole point of having a priority list. Also used
    as the endpoint form's "add a model" suggestion pool, which needs the
    FULL catalog, not just the priority subset -- see priority_ordered_models
    above for the pre-fill-only version.
    """
    ordered = priority_ordered_models(provider, live_models)
    seen = set(ordered)
    for candidate_model in live_models:
        if candidate_model not in seen:
            seen.add(candidate_model)
            ordered.append(candidate_model)
    return ordered


def first_concrete_model(provider: ProviderConfig, live_models: list[str]) -> str | None:
    """The single model "test all accounts" (and the Test dropdown's
    default selection) should use: the first entry of ordered_test_models,
    or None if there's nothing to test against at all.
    """
    models = ordered_test_models(provider, live_models)
    return models[0] if models else None


@dataclass
class BatchTestResult:
    account_key: str
    username: str
    model: str | None
    outcome: TestOutcome


async def test_all_accounts(
    gateway_client: GatewayClient,
    config: GatewayConfig,
    config_manager: ConfigManager,
    provider_name: str,
    live_models: list[str],
    admin_config: AdminConfig,
) -> list[BatchTestResult]:
    """Test every account for a provider, respecting admin.test_concurrency
    and admin.test_delay_seconds so a burst of calls can't itself trip a
    free-tier rate limit on a provider nothing has been tested against yet.
    Concurrency=1 (the default) is fully sequential, paced by the delay.
    """
    provider = config.providers[provider_name]
    model = first_concrete_model(provider, live_models)
    semaphore = asyncio.Semaphore(max(admin_config.test_concurrency, 1))

    async def run_one(account_name: str) -> BatchTestResult:
        account_config = provider.accounts.get(account_name)
        username = (account_config.username if account_config else None) or account_name

        async with semaphore:
            if model is None:
                outcome = TestOutcome(
                    success=False,
                    status_code=None,
                    failure_type=None,
                    message="No models configured for this provider -- add one before testing.",
                )
            else:
                candidate = build_test_candidate(config, config_manager, provider_name, account_name, model)
                outcome = await test_candidate_with_retry(
                    gateway_client, candidate, admin_config.test_retry_delay_seconds
                )
            if admin_config.test_delay_seconds:
                await asyncio.sleep(admin_config.test_delay_seconds)
        return BatchTestResult(account_key=account_name, username=username, model=model, outcome=outcome)

    tasks = [run_one(account_name) for account_name in provider.accounts]
    return list(await asyncio.gather(*tasks))
