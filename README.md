# AI Gateway

A lightweight, OpenAI-compatible gateway that orchestrates **free-tier LLM
APIs** across multiple providers and accounts, sitting in front of
[LiteLLM](https://github.com/BerriAI/litellm).

> Built to solve a real problem: juggling several providers' free tiers
> (Gemini, Groq, OpenRouter, Mistral, GitHub Models...) by hand is
> tedious and brittle. This gateway turns them into a single OpenAI-
> compatible endpoint that automatically rotates across providers,
> models, and accounts when one fails or runs out of quota — with a
> web admin UI for managing it all without hand-editing YAML.

**At a glance:** Python / FastAPI · Pydantic-validated YAML config with
a generated JSON Schema · deterministic multi-provider request routing
with failure-aware cooldowns · server-rendered admin UI (Jinja2 +
HTMX) · 160+ pytest tests · ~4,000 lines of application code.

See [`DESIGN_NOTES.md`](DESIGN_NOTES.md) for the reasoning behind the
less-obvious design decisions (why cooldowns key on account rather than
model, why `MODEL_NOT_FOUND` never penalizes an account, the LiteLLM
retry bug that cost real debugging time, etc.) — useful context on how
the project evolved, beyond what the README above documents.

```
OpenAI-compatible clients
                              │
                              ▼
                     AI Gateway (this project)
         ─────────────────────────────────────────
         ConfigManager · CandidateResolver · Router
         FailureClassifier · CooldownManager
         GatewayClient
         ─────────────────────────────────────────
                              │
                              ▼
                LiteLLM (transport only, no accounts)
                              │
                              ▼
         Gemini · Groq · OpenRouter · DeepSeek · ...
```

## What this is (and isn't)

The gateway is **not** a LiteLLM replacement. LiteLLM remains the only
component that talks to provider SDKs and handles OpenAI-format
translation. The gateway owns everything *around* that:

- configuration (YAML, validated against a JSON Schema)
- provider grouping and account pools
- deterministic routing across provider → model → account
- failure classification and per-account cooldowns
- retries and automatic rotation when an account is exhausted

Accounts (API keys) are **never** configured inside LiteLLM. The gateway
is the single source of truth for accounts and injects the correct API
key per request, so there is only one place to add or remove a key.

## How routing works

Each **endpoint** (e.g. `planner`, `coder`) is a virtual model name that
clients select via the standard OpenAI `model` field. At startup, the
gateway asks LiteLLM which models are actually available, resolves any
wildcards (`gemini-2.5-*`) against that list, and compiles the entire
provider → model → account tree into a flat, ordered list of
**candidates** per endpoint. Routing a request is then just: try each
candidate in order, skip anything in cooldown or disabled, stop at the
first success.

```
planner
  → [gemini/gemini-2.5-flash/personal,
     gemini/gemini-2.5-flash/work,
     gemini/gemini-2.5-pro/personal,
     groq/llama-4-scout/primary, ...]
```

If a candidate fails, `FailureClassifier` turns the raw HTTP status /
network error into one of seven generic failure types (never anything
provider-specific), and `CooldownManager` reacts accordingly:

| Failure type | Behavior |
|---|---|
| `AUTH_FAILURE` | Account is **disabled** immediately (retrying a bad key on a timer is pure waste) |
| `QUOTA_EXHAUSTED` | Long fixed cooldown (quota resets on a clock, not a backoff curve) |
| `MODEL_NOT_FOUND` (HTTP 404) | **No cooldown at all** -- this is a model-identity problem (typo, deprecated ID, wrong provider path), not an account-health problem, so the account isn't penalized. Only counts against `routing.max_attempts` if the failed call took at least `model_not_found_count_threshold_seconds` (default 2.0s) -- a fast rejection is treated as free, a slow one still consumes attempt budget |
| `TEMP_RATE_LIMIT` / `TRANSIENT` / `NETWORK_FAILURE` | Exponential backoff, capped |
| `UNKNOWN` | Short fixed cooldown (conservative default) |

A global `routing.max_attempts` cap bounds worst-case latency regardless
of how large the candidate pool is.

### Filtering non-chat model variants out of a wildcard

A broad wildcard like `gemini-2.5-*` also matches non-chat variants a
provider ships under similar names -- TTS, image generation, embeddings,
live-audio, computer-use, and so on. These reject a normal
`/v1/chat/completions` request with an HTTP 400 (a legitimate rejection,
not a bug), and by default that costs the account a short `UNKNOWN`
cooldown before falling through to a real chat model. Rather than hand-
narrowing every wildcard, exclude the pattern once per provider:

```yaml
providers:
  gemini:
    defaults:
      models: ["gemini-2.5-*"]
    exclude_models: ["*-tts", "*-image*", "*-audio-*", "*embedding*"]
```

`exclude_models` is applied after inclusion patterns are expanded, and
removes a match even if it came from an explicit literal name rather than
a wildcard.

## Configuration

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

### Configuring LiteLLM itself

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

### Wildcard model discovery — provider support varies

The gateway's own wildcard matching (`gemini-2.5-*` → concrete models) is
pure string matching against whatever LiteLLM's `/v1/models` reports; the
gateway does no provider-specific parsing. But LiteLLM's ability to
*expand* a provider's real catalog into that list depends on the provider
— confirm current support before relying on a wildcard for a given
provider, and fall back to explicit model names otherwise.

Set `routing.strict_model_validation: true` to fail startup (or reject a
`/internal/reload`) if a literal, explicitly-configured model name isn't
found in LiteLLM's reported list — useful for catching typos and stale
model IDs before they surface as runtime 404s. Left `false` (the default),
unresolved literal models are logged in one consolidated warning and
included in routing anyway.

### Restricting which models LiteLLM will serve

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
  explicit path above overrides this for that one file (see Admin UI section)

## API

- `POST /v1/chat/completions` — `model` selects an endpoint (e.g.
  `"planner"`), not a real provider model. Streaming (`stream: true`) is
  **not supported in v1** and returns `400`; failing over mid-stream is a
  fundamentally different problem than failing over before the first byte.
- `GET /v1/models` — returns the configured **endpoints** (virtual models),
  not underlying provider model names.
- `GET /health` — LiteLLM reachability plus a snapshot of every account's
  cooldown/disabled state.
- `POST /internal/reload` — re-reads the YAML config and recompiles
  candidates without a restart. Cooldown/disabled state for accounts that
  still exist afterward is preserved (it lives in `CooldownManager`, keyed
  by `provider:account`, independent of the candidate list).

## Admin UI

A local, server-rendered admin UI is available at `/admin/` for managing
providers, accounts, priority models, and endpoints without hand-editing
`config.yaml` or `.env`. Since the last full description of this section,
also added: accounts are **reorderable** (same up/down/top/bottom controls
as endpoint entries) and their internal key/label is **renameable**
separately from username (endpoint references to a renamed account are
updated automatically); an optional **label** field on add/edit overrides
the auto-generated key outright (e.g. `personal` instead of an
auto-slugified `one_anon_201`); a **show/hide API keys** toggle on the
accounts table (hidden by default, no layout shift when toggled); and a
**"Create backup now"** button on the dashboard for an on-demand snapshot
of both `config.yaml` and `.env` together, on top of the automatic backup
already made before every write.

- **Providers**: add/edit/delete, with a curated suggestion list for common
  free-tier providers (Gemini, Groq, OpenRouter, Mistral, Cerebras, GitHub
  Models, Cloudflare Workers AI, SambaNova, Z.ai/GLM, Ollama Cloud, Qwen,
  ...) plus free-text entry for anything else, and an optional comment.
- **Accounts**: add/edit/delete per provider. **`username` is the only
  identity you ever need to think about** -- free text, shown exactly as
  typed everywhere (an email address, a nickname, whatever), and freely
  editable later. Everything else about an account's identity is generated
  and hidden: a stable internal key (used only as the `config.yaml` dict
  key and admin URL segment) and an env var name, both derived once at
  creation and never something you need to manage. Also editable per
  account, independently: the **API key** (rotate -- overwrites the same
  `.env` value, takes effect immediately) and the **env var name** itself
  (rename -- copies the value to a new `.env` line, old line left in place
  unused), plus a free-text comment.
- **Test / Test all accounts**: fires real call(s) through the same
  `GatewayClient`/`FailureClassifier` path production traffic uses --
  deliberately bypassing `CooldownManager`, since a manual test must never
  mark an account healthy or disabled. A failed test gets **one automatic
  retry**, but only for `TRANSIENT`/`NETWORK_FAILURE` outcomes (never
  `AUTH_FAILURE`, `MODEL_NOT_FOUND`, or `QUOTA_EXHAUSTED`, which retrying
  can't change). "Test all accounts" for a provider is paced by
  `admin.test_concurrency` / `admin.test_delay_seconds` in `config.yaml`
  (default: fully sequential, 0.3s apart) specifically so a bulk test run
  can't itself trip a free tier's per-minute limit -- raise
  `test_concurrency` only once you know your accounts' actual limits.
- **Models**: a priority-ordered list per provider (add via a filterable
  text input suggesting LiteLLM's live reported models, freeform entry
  still allowed for wildcards), reorderable, each with an optional
  comment, plus the exclude-patterns list. The wildcard registration
  string itself (e.g. a bare `*` once LiteLLM's own `gemini/*` deployment
  entry is prefix-stripped) is filtered out of every suggestion/selection
  list -- it was never a valid model to select or test against.
- **Endpoints**: add/edit/delete, with an optional comment. Provider
  entries are **reorderable** (up/down, move to top/bottom) and the same
  provider can appear more than once in one endpoint -- e.g. `gemini` ->
  `groq` -> `gemini` again with different models, tried in exactly that
  order. Each entry always has its own **custom ordered model list**
  (never an implicit "provider defaults" mode): toggling off "use this
  provider's default priority models" seeds the list from the provider's
  current default order as a one-time starting point, then it's freely
  editable and reorderable from there, same picker style as the
  provider's own model editor. An optional **"include remaining models"**
  checkbox per entry appends every other live model for that provider
  (exclude-filtered) as low-priority fallback -- resolved fresh at every
  startup/reload, never baked into a snapshot, and stored as a single
  `include_remaining_models: true` flag that's simply omitted from
  `config.yaml` when left off, so it doesn't clutter entries that don't
  use it. Providers/models/accounts are all selected via
  filter+bulk-select checklists or the ordered picker -- never free text
  -- which is what keeps this safe at high account counts. Duplicate
  provider entries get a small "(#2)" label so it's clear at a glance
  which is which.

Every write goes: modify a `GatewayConfig` object in memory -> validate
the *whole* object with the same Pydantic model `ConfigManager` uses at
startup -> only on success, back up the current `config.yaml`
(microsecond-precision timestamp, so rapid successive edits never
collide) and overwrite it -> recompile and hot-swap candidates via the
same path `/internal/reload` uses. Nothing is ever hand-templated as YAML
text, so a malformed `config.yaml` should be structurally impossible to
produce through this UI. The same backup mechanism applies to every
`.env` write (add/rotate/rename/delete an account) -- a mis-click on
Delete is recoverable from a `.env.<timestamp>.bak` file, not just gone.

Secrets never pass through `config.yaml`: adding an account writes the
key to a dotenv file (see `AI_GATEWAY_ENV_FILE` below) and immediately
sets it in the running process's environment, so it's usable without a
restart. Deleting an account removes it from `config.yaml` but
deliberately leaves the `.env` line in place.

**This UI is unauthenticated by design**, matching this project's
trusted-local-network assumption -- do not expose it beyond localhost or
a trusted network.

### A test button showing a vague failure? Check these first

- **Your launch `--env-file` and `AI_GATEWAY_ENV_FILE` must point at the
  same dotenv file.** If they don't, the admin UI writes secrets one place
  while the running process reads from another, and every account will
  look broken. A `CONFIG_ERROR` badge with "No value loaded for
  environment variable ..." is telling you exactly this -- it's a
  short-circuit before any network call, not a network failure.
- **LiteLLM needs `router_settings: { num_retries: 0 }`** in its own
  config (see `litellm-config.example.yaml`). Without it, LiteLLM applies
  its own default internal retry count, and on those internal retries it
  can silently drop the per-request `api_key` override -- producing a
  request that succeeds or fails at random on identical input. Confirmed
  in practice; not optional.
- Any other test failure now shows its actual diagnostic message directly
  in the UI (not hidden behind a tooltip) -- read it before assuming it's
  this project's bug rather than an upstream one.

Environment variables:
- `AI_GATEWAY_ENV_FILE` -- which dotenv file the admin UI upserts secrets
  into (default `.env`). This is separate from whatever your launcher
  used to populate the process's initial environment (e.g. `uv run
  --env-file ../.env`), since the running process has no way to know that
  path on its own. **Keep these two in sync** -- see above.

## Development

```bash
uv sync --extra dev
uv run pytest
```

## Design constraints (why it's this small)

- No microservices, no Redis, no SQL/ORM, no Celery, no message queues, no
  background workers, no provider SDKs, no plugin system.
- Cooldown state lives in process memory only. A restart briefly
  re-enables a genuinely dead account until it fails once more — an
  accepted tradeoff for staying dependency-free.
- Six components, matching the approved architecture: `ConfigManager`,
  `CandidateResolver`, `Router`, `FailureClassifier`, `CooldownManager`,
  `GatewayClient`. `GatewayClient` is the *only* class that knows LiteLLM
  exists; replacing the transport later should only require changing that
  one file.

## License

MIT
