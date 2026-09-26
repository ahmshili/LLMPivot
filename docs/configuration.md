Part of the [LLMPivot](../README.md) documentation.

# Configuration

## Table of contents

- [Configuring LiteLLM itself](#configuring-litellm-itself)
- [Restricting which models LiteLLM will serve](#restricting-which-models-litellm-will-serve)
- [Running](#running)

Copy `config.example.yaml` to `config.yaml` and `.env.example` to `.env`,
then fill in real values. The example file documents every field; a JSON
Schema (`config.schema.json`) is generated directly from the Pydantic
models that validate it, so schema and code can't drift apart. Point your
editor at it via the `# yaml-language-server: $schema=...` header for
autocompletion and inline validation.

Secrets are never stored in YAML. Each account resolves to an environment
variable, defaulting to `{PROVIDER}_{ACCOUNT}_API_KEY` (e.g.
`GEMINI_PERSONAL_API_KEY`), or an explicit `api_key_env:` override.

To regenerate the schema after changing a config model:

```bash
uv run python scripts/generate_schema.py
```

## Configuring LiteLLM itself

`litellm-config.example.yaml` is a companion file for your **LiteLLM**
deployment (not part of this package) that this gateway expects:

- Wildcard deployments (`gemini/*`, `groq/*`, `openrouter/*`) so the
  gateway's `{litellm_prefix}{model}` strings always match a registered
  deployment, instead of relying on LiteLLM's undocumented ad-hoc
  pass-through behavior for unregistered models.
- `configurable_clientside_auth_params: ["api_key"]` on every deployment,
  required for the gateway's per-request account credential injection to
  actually take effect.
- No `router_settings.fallbacks` / `num_retries` — LiteLLM must not do its
  own routing or retries, or its cooldown/account tracking silently
  diverges from the gateway's.
- `check_provider_endpoint: true` for Gemini only. This makes LiteLLM's
  `/v1/models` expand into Gemini's real model catalog, which is what lets
  the gateway's `gemini-2.5-*` wildcard resolve to concrete models at
  startup. As of this writing, Groq and OpenRouter are not on LiteLLM's
  documented support list for this feature — use literal (non-wildcard)
  model names for those providers in the gateway's own `config.yaml`
  instead of wildcards.

## Restricting which models LiteLLM will serve

By default, LiteLLM has **no authentication** (`general_settings.master_key`
is unset), so anything reachable on its port can call any configured
model. To lock this down, toggled independently on each side:

1. **LiteLLM side** (`litellm-config.example.yaml`): uncomment
   `general_settings.master_key`, then generate a virtual key scoped to
   `models: ["gemini/*", "groq/*", "openrouter/*"]` (see the comments in
   that file for the exact `curl` command) instead of using the master key
   for day-to-day traffic.
2. **Gateway side** (`config.yaml`): set `litellm.proxy_api_key_env` to the
   environment variable holding that key. The gateway sends it as
   `Authorization: Bearer <key>` on every call to LiteLLM.

Both sides must agree — enabling only one will 401 every request. A
misconfiguration here is caught at gateway startup (the initial
`/v1/models` call fails loudly) rather than silently, which matters
because a 401 on every request would otherwise look identical to every
single account having a bad API key, and `CooldownManager` would disable
all of them.

## Running

You'll need a LiteLLM instance running as a plain OpenAI-compatible
transport (no accounts configured on its side — just model name → provider
mappings, e.g. `gemini/*` routed to the Gemini SDK).

```bash
uv sync
uv run uvicorn ai_gateway.main:app --host 0.0.0.0 --port 8000
```

Or via the friendlier console script, which adds real CLI flags on top of
the same environment variables (most usefully `--data-dir`, for keeping
`config.yaml` and `.env` in a folder outside the project checkout, e.g. a
sibling directory shared with your LiteLLM deployment):

```bash
uv run ai-gateway --data-dir ../data --port 8000
```

**Adding a new provider through the admin UI only ever touches this
gateway's own `config.yaml`.** It cannot register anything with LiteLLM,
which is a separate service with its own config file. Every provider you
add here also needs a matching `{prefix}/*` wildcard deployment added by
hand to LiteLLM's `config.yaml` (see `litellm-config.example.yaml`) —
skipping this is the most common cause of a provider showing zero live
models or every test failing with "no healthy deployments."

Environment variables:

- `AI_GATEWAY_CONFIG` — path to the YAML config file (default `config.yaml`)
- `AI_GATEWAY_LOG_LEVEL` — log level (default `INFO`)
- `AI_GATEWAY_DATA_DIR` — base directory for `config.yaml` and `.env`; either
  explicit path above overrides this for that one file (see the admin UI
  docs)
