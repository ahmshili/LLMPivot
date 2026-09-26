"""ConfigManager: the single component that touches the filesystem and
environment variables for configuration purposes.

Responsibilities:
    * Read the YAML file from disk.
    * Parse it into a validated :class:`GatewayConfig`.
    * Resolve each account's environment variable name (explicit or
      convention-based) and record whether the secret is actually present.

ConfigManager does NOT talk to LiteLLM and does NOT build candidates -- that
is ``CandidateResolver``'s job. Keeping this split means CandidateResolver
can be re-run on ``/internal/reload`` without re-reading the file if ever
needed, and can be unit-tested without any YAML at all.
PivotLLM -- https://github.com/ahmshili/LLMPivot -- Copyright (c) ahmshili.
Portfolio project, source-available license (see LICENSE at repo root):
view/evaluate only, no redistribution, no forks outside PRs to the
original repo, no production/commercial use without permission. This
notice must be preserved. Contact: a.shili.pers@gmail.com
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import yaml

from ai_gateway.config.models import GatewayConfig

logger = logging.getLogger("ai_gateway.config")


def default_env_var_name(provider: str, account: str) -> str:
    """Compute the conventional environment variable name for an account:
    ``{PROVIDER}_{ACCOUNT}_API_KEY``, upper-cased with non-alphanumeric
    characters replaced by underscores.
    """
    combined = f"{provider}_{account}_API_KEY".upper()
    return re.sub(r"[^A-Z0-9_]", "_", combined)


class ConfigManager:
    """Loads and validates the gateway's YAML configuration file."""

    def __init__(self, config_path: str | Path) -> None:
        self._config_path = Path(config_path)
        self._config: GatewayConfig | None = None

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def config(self) -> GatewayConfig:
        if self._config is None:
            raise RuntimeError("Configuration has not been loaded yet. Call load() first.")
        return self._config

    def load(self) -> GatewayConfig:
        """Read and validate the YAML file, resolve account environment
        variable names, and warn (without raising) about any missing
        secrets. Missing secrets do not prevent startup -- an account with
        no resolvable key is simply excluded from candidate resolution.
        """
        if not self._config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {self._config_path}")

        raw_text = self._config_path.read_text(encoding="utf-8")
        raw_data = yaml.safe_load(raw_text) or {}
        if not isinstance(raw_data, dict):
            raise ValueError("Configuration file must contain a YAML mapping at the top level.")

        config = GatewayConfig.model_validate(raw_data)
        self._warn_on_missing_secrets(config)
        self._config = config
        logger.info(
            "Loaded configuration from %s: %d provider(s), %d endpoint(s).",
            self._config_path,
            len(config.providers),
            len(config.endpoints),
        )
        return config

    def resolve_api_key_env(self, provider: str, account: str) -> str:
        """Return the environment variable name for a given provider/account,
        applying the explicit override if one was configured.
        """
        account_config = self.config.providers[provider].accounts.get(account)
        if account_config is not None and account_config.api_key_env:
            return account_config.api_key_env
        return default_env_var_name(provider, account)

    def _warn_on_missing_secrets(self, config: GatewayConfig) -> None:
        for provider_name, provider in config.providers.items():
            for account_name, account in provider.accounts.items():
                env_var = (
                    account.api_key_env
                    if account is not None and account.api_key_env
                    else default_env_var_name(provider_name, account_name)
                )
                if not os.environ.get(env_var):
                    logger.warning(
                        "Account '%s' for provider '%s' has no value set for "
                        "environment variable '%s'. This account will be "
                        "excluded from routing until the variable is set.",
                        account_name,
                        provider_name,
                        env_var,
                    )
