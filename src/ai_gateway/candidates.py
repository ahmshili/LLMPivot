"""Candidate compilation.

Instead of the router walking a live Provider -> Model -> Account tree on
every request, we compile the entire tree into a flat, ordered list of
``Candidate`` objects once at startup (and again on ``/internal/reload``).
Routing then becomes a simple iteration over a precomputed list -- no
nested loops, no regex evaluation, at request time.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from dataclasses import dataclass

from ai_gateway.config.manager import ConfigManager
from ai_gateway.config.models import GatewayConfig, ModelEntry

logger = logging.getLogger("ai_gateway.candidates")


class ModelValidationError(Exception):
    """Raised at startup when routing.strict_model_validation is true and
    one or more literal (non-wildcard) configured models were not found in
    LiteLLM's reported /v1/models list.
    """


def pattern_text(entry: str | ModelEntry) -> str:
    """Normalize a models-list entry (bare string or {pattern, comment}
    object) down to its pattern text. Comments are metadata only and never
    affect resolution.
    """
    return entry.pattern if isinstance(entry, ModelEntry) else entry


def is_entry_enabled(entry: str | ModelEntry) -> bool:
    """A bare string entry is always enabled (there's nowhere to store
    `enabled: false` on a plain string -- that's exactly why a disabled
    entry must be a {pattern, enabled: false} object). A ModelEntry
    defaults to enabled unless explicitly disabled.
    """
    return entry.enabled if isinstance(entry, ModelEntry) else True


def build_litellm_model(litellm_prefix: str, model: str) -> str:
    """Prefix a model name for LiteLLM, without double-prefixing if `model`
    already starts with the provider's litellm_prefix -- e.g. if someone
    typed or pasted "mistral/codestral-latest" into a model field for a
    provider whose prefix is already "mistral/", the naive f"{prefix}{model}"
    concatenation would produce "mistral/mistral/codestral-latest", which
    LiteLLM then rejects as an unrecognized deployment. Provider-agnostic:
    applies to any provider, not just the one that surfaced this.
    """
    if model.startswith(litellm_prefix):
        return model
    return f"{litellm_prefix}{model}"


@dataclass(frozen=True)
class Candidate:
    """A single, fully-resolved (endpoint, provider, model, account) tuple
    that the router can attempt directly, with everything GatewayClient
    needs to make the call already attached.
    """

    endpoint: str
    provider: str
    model: str
    account: str
    litellm_model: str
    api_key_env: str
    api_base: str | None

    @property
    def key(self) -> str:
        """Cooldown lookup key. Deliberately scoped to (provider, account)
        and not to the endpoint: an account exhausted via one endpoint is
        exhausted everywhere, since it's the same underlying API key.
        """
        return f"{self.provider}:{self.account}"

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return f"{self.endpoint}/{self.provider}/{self.model}/{self.account}"


def _compile_wildcard(pattern: str) -> re.Pattern[str]:
    """Compile a glob-style wildcard (e.g. 'gemini-2.5-*') into a regex."""
    return re.compile(fnmatch.translate(pattern))


def _is_wildcard(pattern: str) -> bool:
    return any(ch in pattern for ch in "*?[]")


class CandidateResolver:
    """Compiles a :class:`GatewayConfig` plus the model list reported by
    LiteLLM into an ordered candidate list per endpoint.

    Wildcards are compiled into regex exactly once, here, at construction
    time. Nothing downstream ever evaluates a regex again.
    """

    def __init__(self, config_manager: ConfigManager, available_models: list[str]) -> None:
        self._config_manager = config_manager
        self._config: GatewayConfig = config_manager.config
        self._available_models = available_models

    def resolve(self) -> dict[str, list[Candidate]]:
        """Return an ordered candidate list for every configured endpoint.

        Raises:
            ModelValidationError: if ``routing.strict_model_validation`` is
                true and any literal (non-wildcard) configured model was not
                found in LiteLLM's reported model list.
        """
        provider_models = {
            name: self._resolve_provider_models(name)
            for name in self._config.providers
        }
        # Compiled once here, alongside the inclusion wildcards, per the
        # same "compile regex once at startup, never at request time" rule.
        exclude_regexes = {
            name: [_compile_wildcard(p) for p in provider.exclude_models]
            for name, provider in self._config.providers.items()
        }

        # Collected across all endpoints/providers so we can log (and
        # optionally fail) once with a full picture, instead of scattering
        # one warning per model across the log.
        unresolved_literals: list[tuple[str, str]] = []
        unmatched_wildcards: list[tuple[str, str]] = []
        excluded_models: list[tuple[str, str]] = []

        result: dict[str, list[Candidate]] = {}
        for endpoint_name, endpoint in self._config.endpoints.items():
            candidates: list[Candidate] = []
            for entry in endpoint.providers:
                provider_config = self._config.providers[entry.name]
                litellm_prefix = provider_config.litellm_prefix or f"{entry.name}/"

                models: list[str | ModelEntry] = (
                    entry.models if entry.models is not None else provider_config.defaults.models
                )
                resolved_models = self._resolve_model_list(
                    entry.name, models, provider_models[entry.name],
                    unresolved_literals, unmatched_wildcards,
                    exclude_regexes[entry.name], excluded_models,
                )
                if entry.include_remaining_models:
                    resolved_models = self._append_remaining_models(
                        resolved_models, provider_models[entry.name],
                        exclude_regexes[entry.name], excluded_models, entry.name,
                    )

                account_names = (
                    entry.accounts if entry.accounts is not None else list(provider_config.accounts)
                )

                for model in resolved_models:
                    for account_name in account_names:
                        api_key_env = self._config_manager.resolve_api_key_env(entry.name, account_name)
                        if not os.environ.get(api_key_env):
                            # Already warned about at config-load time; skip silently here.
                            continue
                        account_config = provider_config.accounts.get(account_name)
                        api_base = account_config.api_base if account_config else None
                        candidates.append(
                            Candidate(
                                endpoint=endpoint_name,
                                provider=entry.name,
                                model=model,
                                account=account_name,
                                litellm_model=build_litellm_model(litellm_prefix, model),
                                api_key_env=api_key_env,
                                api_base=api_base,
                            )
                        )
            result[endpoint_name] = candidates
            logger.info(
                "Endpoint '%s' compiled to %d candidate(s).", endpoint_name, len(candidates)
            )

        self._report_resolution_issues(unresolved_literals, unmatched_wildcards, excluded_models)
        return result

    def _report_resolution_issues(
        self,
        unresolved_literals: list[tuple[str, str]],
        unmatched_wildcards: list[tuple[str, str]],
        excluded_models: list[tuple[str, str]],
    ) -> None:
        """Log one consolidated block covering every model-resolution issue
        found across the whole config, instead of one log line per model.
        Raises ModelValidationError if strict_model_validation is enabled
        and any literal model was unresolved.
        """
        # De-duplicate: the same (provider, model) pattern can appear across
        # multiple endpoints.
        unresolved_literals = sorted(set(unresolved_literals))
        unmatched_wildcards = sorted(set(unmatched_wildcards))
        excluded_models = sorted(set(excluded_models))

        if excluded_models:
            lines = "\n".join(f"  - {provider}: '{model}'" for provider, model in excluded_models)
            logger.info(
                "The following model(s) were removed from routing by "
                "exclude_models:\n%s",
                lines,
            )

        if unmatched_wildcards:
            lines = "\n".join(f"  - {provider}: '{pattern}'" for provider, pattern in unmatched_wildcards)
            logger.warning(
                "The following wildcard(s) matched no models reported by "
                "LiteLLM's /v1/models (this is expected for providers "
                "LiteLLM cannot enumerate, e.g. Groq/OpenRouter without "
                "check_provider_endpoint support -- consider using literal "
                "model names for those providers instead):\n%s",
                lines,
            )

        if unresolved_literals:
            lines = "\n".join(f"  - {provider}: '{model}'" for provider, model in unresolved_literals)
            if self._config.routing.strict_model_validation:
                raise ModelValidationError(
                    "routing.strict_model_validation is enabled and the "
                    f"following configured model(s) were not found in "
                    f"LiteLLM's /v1/models response:\n{lines}\n"
                    "Fix the model names, register them in LiteLLM, or "
                    "disable strict_model_validation."
                )
            logger.warning(
                "The following configured model(s) were NOT found in "
                "LiteLLM's /v1/models response. They are included in "
                "routing anyway since they were explicitly configured, but "
                "this often means a typo or a missing LiteLLM deployment:\n%s",
                lines,
            )

    def _append_remaining_models(
        self,
        resolved_models: list[str],
        provider_models: list[str],
        exclude_regexes: list[re.Pattern[str]],
        excluded_models: list[tuple[str, str]],
        provider_name: str,
    ) -> list[str]:
        """Append every other live model for this provider (in LiteLLM's
        reported order, exclude-filtered) after the entry's own resolved
        list, for entries with include_remaining_models set. Re-run fresh
        on every resolve() call -- this never bakes a snapshot into
        config.yaml, unlike the entry's own `models` list.
        """
        seen = set(resolved_models)
        result = list(resolved_models)
        for model in provider_models:
            if model in seen:
                continue
            if any(rx.match(model) for rx in exclude_regexes):
                excluded_models.append((provider_name, model))
                continue
            seen.add(model)
            result.append(model)
        return result

    def _resolve_provider_models(self, provider_name: str) -> list[str]:
        """Return the list of models (stripped of the LiteLLM prefix) that
        LiteLLM currently reports for this provider, used to expand
        wildcards against what's actually available.
        """
        provider_config = self._config.providers[provider_name]
        prefix = provider_config.litellm_prefix or f"{provider_name}/"
        stripped = []
        for model_id in self._available_models:
            if model_id.startswith(prefix):
                stripped.append(model_id[len(prefix):])
        return stripped

    def _resolve_model_list(
        self,
        provider_name: str,
        patterns: list[str | ModelEntry],
        provider_models: list[str],
        unresolved_literals: list[tuple[str, str]],
        unmatched_wildcards: list[tuple[str, str]],
        exclude_regexes: list[re.Pattern[str]],
        excluded_models: list[tuple[str, str]],
    ) -> list[str]:
        """Expand a list of exact names / wildcards into concrete model
        names, preserving pattern order and de-duplicating, then remove
        anything matching an exclude pattern -- even if it was explicitly
        listed or matched by an inclusion wildcard. Issues are appended to
        the shared lists rather than logged individually, so the caller can
        report them once, consolidated. Entries may be bare strings or
        {pattern, comment, enabled} objects; comments are metadata only
        here. Disabled entries (enabled=False) are skipped entirely --
        they're kept in config.yaml purely for reference and never
        contribute a candidate, so they also never trigger an
        unresolved-literal/unmatched-wildcard warning.
        """
        resolved: list[str] = []
        seen: set[str] = set()
        for raw_entry in patterns:
            if not is_entry_enabled(raw_entry):
                continue
            pattern = pattern_text(raw_entry)
            if _is_wildcard(pattern):
                regex = _compile_wildcard(pattern)
                matches = [m for m in provider_models if regex.match(m)]
                if not matches:
                    unmatched_wildcards.append((provider_name, pattern))
                for match in matches:
                    if match not in seen:
                        seen.add(match)
                        resolved.append(match)
            else:
                if pattern not in provider_models:
                    unresolved_literals.append((provider_name, pattern))
                if pattern not in seen:
                    seen.add(pattern)
                    resolved.append(pattern)

        if not exclude_regexes:
            return resolved

        filtered: list[str] = []
        for model in resolved:
            if any(rx.match(model) for rx in exclude_regexes):
                excluded_models.append((provider_name, model))
                continue
            filtered.append(model)
        return filtered
