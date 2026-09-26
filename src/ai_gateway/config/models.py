"""Pydantic models describing the gateway's YAML configuration.

These models are the single source of truth for configuration shape. The
JSON Schema shipped alongside the example config (``config.schema.json``) is
generated directly from :class:`GatewayConfig`, so the two can never drift
apart.

Nothing in this module talks to LiteLLM, the filesystem, or environment
variables -- it only describes *shape* and *static* validation rules (e.g.
"an endpoint must reference a provider that exists"). Reading files,
resolving environment variables, and reaching out to LiteLLM all happen in
``ai_gateway.config.manager``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelEntry(BaseModel):
    """A model pattern with an optional comment attached, and an optional
    enabled flag.

    Kept separate from a bare string so plain patterns (the overwhelming
    majority) still serialize as plain strings -- only entries that
    actually have a comment or are disabled pay for the richer object
    form. `enabled` defaults to True and is excluded from serialization
    in that case (config_writer uses `exclude_defaults=True`), so a
    normal, always-active entry never gains this field in config.yaml --
    only entries the user has actually disabled show `enabled: false`.
    """

    model_config = ConfigDict(extra="forbid")

    pattern: str
    comment: str | None = None
    enabled: bool = True


class ProviderDefaults(BaseModel):
    """Default model list for a provider, used when an endpoint does not
    override which models to use for that provider.

    Entries may be exact model names (``"gemini-2.5-flash"``) or glob-style
    wildcards (``"gemini-2.5-*"``), each either a bare string or a
    ``{pattern, comment}`` object. Wildcards are compiled into regex once
    at startup by ``CandidateResolver`` and matched against the model list
    LiteLLM reports at ``/v1/models``.
    """

    model_config = ConfigDict(extra="forbid")

    models: list[str | ModelEntry] = Field(
        default_factory=list,
        description="Exact model names or glob wildcards (e.g. 'gemini-2.5-*'), optionally with a comment.",
    )
    standard_models: list[str | ModelEntry] = Field(
        default_factory=list,
        description=(
            "A saved pool of models kept for quick-selection/fallback, NOT part of active "
            "routing priority -- CandidateResolver never reads this list. Mutually exclusive "
            "with `models`: a pattern should never appear in both lists at once (enforced by "
            "the admin UI's move actions, and defensively re-enforced on save -- see "
            "admin/routes.py's save_models, priority always wins any conflict)."
        ),
    )


class AccountConfig(BaseModel):
    """A single account (API key) belonging to a provider.

    Accounts intentionally hold no secrets in YAML. ``api_key_env`` names an
    environment variable that holds the actual key; if omitted, it defaults
    to ``{PROVIDER}_{ACCOUNT}_API_KEY`` (both upper-cased, non-alphanumeric
    characters replaced with ``_``).

    ``username`` is a free-text, never-sanitized, always-editable display
    name -- the only user-facing identity for the account. It is entirely
    separate from the account's config.yaml dict key / admin URL segment,
    which is a stable slug generated once at creation and not intended to
    be seen or edited directly.
    """

    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(
        default=None,
        description="Free-text display name for this account (e.g. an email address). Never sanitized.",
    )
    api_key_env: str | None = Field(
        default=None,
        description=(
            "Name of the environment variable holding this account's API "
            "key. Defaults to {PROVIDER}_{ACCOUNT}_API_KEY if omitted."
        ),
    )
    api_base: str | None = Field(
        default=None,
        description="Optional per-account API base override, forwarded to LiteLLM.",
    )
    comment: str | None = Field(default=None, description="Free-text operator note.")


class ProviderConfig(BaseModel):
    """A provider groups a LiteLLM prefix, a default model list, and a pool
    of accounts (API keys) that can serve requests for that provider.
    """

    model_config = ConfigDict(extra="forbid")

    litellm_prefix: str | None = Field(
        default=None,
        description=(
            "Prefix LiteLLM expects for this provider's models, e.g. "
            "'gemini/'. Defaults to '{provider_name}/' if omitted."
        ),
    )
    defaults: ProviderDefaults = Field(default_factory=ProviderDefaults)
    exclude_models: list[str] = Field(
        default_factory=list,
        description=(
            "Glob patterns for models to remove after expansion, even if "
            "explicitly listed or matched by a wildcard (e.g. ['*-tts', "
            "'*-image*', '*-audio*'] to keep a broad chat-model wildcard "
            "from picking up non-chat variants)."
        ),
    )
    accounts: dict[str, AccountConfig | None] = Field(
        default_factory=dict,
        description="Named accounts for this provider. A null value uses all defaults.",
    )
    comment: str | None = Field(default=None, description="Free-text operator note.")


class EndpointProviderConfig(BaseModel):
    """A single provider entry within an endpoint's priority list.

    ``models`` and ``accounts``, when omitted, fall back to the provider's
    ``defaults.models`` and the provider's full account list respectively.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    models: list[str] | None = Field(
        default=None,
        description="Override model list for this provider within this endpoint.",
    )
    accounts: list[str] | None = Field(
        default=None,
        description="Override account list for this provider within this endpoint.",
    )
    include_remaining_models: bool = Field(
        default=False,
        description=(
            "If true, every other live model for this provider (after "
            "exclude_models filtering) is appended after `models`, "
            "re-resolved dynamically at every startup/reload -- never "
            "baked into a snapshot. Omitted (false) by default, so this "
            "never appears in config.yaml unless explicitly turned on."
        ),
    )


class EndpointConfig(BaseModel):
    """An endpoint is a virtual, capability-named model (e.g. 'planner')
    that clients select via the OpenAI ``model`` field. It resolves, in
    priority order, to a list of provider entries.
    """

    model_config = ConfigDict(extra="forbid")

    providers: list[EndpointProviderConfig] = Field(default_factory=list, min_length=1)
    comment: str | None = Field(default=None, description="Free-text operator note.")


class LiteLLMConfig(BaseModel):
    """Connection settings for the LiteLLM transport backend."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(default="http://localhost:4000")
    timeout_seconds: float = Field(default=30.0, gt=0)
    proxy_api_key_env: str | None = Field(
        default=None,
        description=(
            "Name of the environment variable holding the key used to "
            "authenticate to the LiteLLM proxy itself (its general_settings."
            "master_key, or a scoped virtual key). This is separate from "
            "per-account provider API keys, which are injected per request. "
            "If unset, no Authorization header is sent to LiteLLM -- this "
            "is the on/off toggle for proxy-side auth."
        ),
    )


class RoutingConfig(BaseModel):
    """Global routing limits, independent of pool size, so an exhausted
    provider cannot turn a single request into dozens of sequential calls.
    """

    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=8, ge=1)
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    stream_read_timeout_seconds: float = Field(
        default=300.0,
        gt=0,
        description=(
            "Read timeout used only for the streaming path, applied to the gap "
            "between individual chunks, not total response time. Deliberately "
            "much more generous than request_timeout_seconds: a long pause "
            "between SSE chunks during legitimate generation (long agentic "
            "code-generation responses in particular) isn't a stuck request, "
            "and httpx applies a single timeout value to every category "
            "(connect/read/write/pool) unless told otherwise -- using "
            "request_timeout_seconds here as well would kill a healthy, "
            "still-generating stream just because two tokens took a while to "
            "arrive. Connect/write/pool for the streaming path still use "
            "request_timeout_seconds, so a genuinely unreachable provider "
            "still fails fast at connection time."
        ),
    )
    strict_model_validation: bool = Field(
        default=False,
        description=(
            "If true, a literal (non-wildcard) configured model that is not "
            "found in LiteLLM's reported /v1/models list fails startup "
            "instead of being logged as a warning and excluded from routing."
        ),
    )
    model_not_found_count_threshold_seconds: float = Field(
        default=2.0,
        gt=0,
        description=(
            "A MODEL_NOT_FOUND failure never affects account cooldown "
            "state, but it only counts against max_attempts if the failed "
            "call took at least this long -- a fast rejection is treated "
            "as free, while a slow one (comparable to a real model call) "
            "still consumes part of the attempt budget."
        ),
    )


class CooldownConfig(BaseModel):
    """Cooldown durations for each failure category.

    ``base_seconds`` / ``multiplier`` / ``max_seconds`` govern exponential
    backoff for transient-style failures. Quota exhaustion and unknown
    failures use their own fixed durations rather than the backoff curve,
    since "quota exhausted" is a clock-based reset, not a retry-storm signal.
    """

    model_config = ConfigDict(extra="forbid")

    base_seconds: float = Field(default=30.0, gt=0)
    multiplier: float = Field(default=2.0, gt=1)
    max_seconds: float = Field(default=3600.0, gt=0)
    quota_cooldown_seconds: float = Field(default=3600.0, gt=0)
    unknown_failure_cooldown_seconds: float = Field(default=60.0, gt=0)


class AdminConfig(BaseModel):
    """Settings for the admin UI's account/model Test buttons -- separate
    from RoutingConfig, since these govern ad-hoc operator probes, not
    production request routing.
    """

    model_config = ConfigDict(extra="forbid")

    test_concurrency: int = Field(
        default=1,
        ge=1,
        description=(
            "How many admin UI test calls run in parallel during a bulk "
            "'test all accounts' run. 1 = fully sequential (safest for "
            "providers with low free-tier per-minute limits). Raise this "
            "only when you understand your accounts' rate limits."
        ),
    )
    test_delay_seconds: float = Field(
        default=0.3,
        ge=0,
        description=(
            "Delay after each admin UI test call before starting the next "
            "one, applied per concurrency slot. Sequential runs (the "
            "default) simply pace one call every test_delay_seconds."
        ),
    )
    test_retry_delay_seconds: float = Field(
        default=1.0,
        ge=0,
        description=(
            "Delay before the single automatic retry of a test call that "
            "failed with TEMP_RATE_LIMIT-adjacent TRANSIENT or "
            "NETWORK_FAILURE outcomes. Other failure types are never "
            "retried, since retrying them can't change the outcome."
        ),
    )


class SecurityConfig(BaseModel):
    """Prod-mode authentication settings. Both stay unenforced by default,
    exactly like everything else today -- enforcement is opt-in via CLI
    flags at startup (--prod, --enable-admin), not automatic just because
    these are configured, so local/trusted-network usage never changes.

    Follows this project's existing secret pattern (proxy_api_key_env,
    each account's api_key_env): config.yaml names an env var, the actual
    secret value lives in .env, never embedded directly here.
    """

    model_config = ConfigDict(extra="forbid")

    api_auth_token_env: str | None = Field(
        default=None,
        description=(
            "Env var holding the bearer token required on /v1/* routes "
            "when prod mode is enabled via --prod. If unset, /v1/* stays "
            "unauthenticated even in prod mode -- an explicit choice "
            "(e.g. already-secured private network), not a silent gap."
        ),
    )
    admin_auth_token_env: str | None = Field(
        default=None,
        description=(
            "Env var holding the bearer token required for /admin/* "
            "routes when explicitly re-enabled in prod mode via "
            "--enable-admin. Deliberately a separate secret from "
            "api_auth_token_env, to keep blast radius contained if one "
            "leaks. Startup fails if --enable-admin is passed but this "
            "isn't configured with a real value -- unlike the API token, "
            "there's no 'intentionally left open' reading for the admin "
            "UI, given what it exposes."
        ),
    )


class GatewayConfig(BaseModel):
    """Root configuration object for LLMPivot."""

    model_config = ConfigDict(extra="forbid")

    litellm: LiteLLMConfig = Field(default_factory=LiteLLMConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    cooldown: CooldownConfig = Field(default_factory=CooldownConfig)
    admin: AdminConfig = Field(default_factory=AdminConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    endpoints: dict[str, EndpointConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_references(self) -> "GatewayConfig":
        """Ensure every endpoint only references providers and accounts that
        actually exist. This is pure structural validation -- no I/O, no
        environment variables, no LiteLLM calls.
        """
        for endpoint_name, endpoint in self.endpoints.items():
            for entry in endpoint.providers:
                provider = self.providers.get(entry.name)
                if provider is None:
                    raise ValueError(
                        f"Endpoint '{endpoint_name}' references unknown "
                        f"provider '{entry.name}'."
                    )
                if entry.accounts is not None:
                    unknown = set(entry.accounts) - set(provider.accounts)
                    if unknown:
                        raise ValueError(
                            f"Endpoint '{endpoint_name}' provider "
                            f"'{entry.name}' references unknown account(s) "
                            f"{sorted(unknown)}."
                        )
        return self
