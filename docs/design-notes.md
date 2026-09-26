Part of the [LLMPivot](../README.md) documentation.

# Design notes

## Table of contents

- [What this document is for](#what-this-document-is-for)
- [The six-class core](#the-six-class-core)
- [Non-obvious decisions and why](#non-obvious-decisions-and-why)
- [Known-fragile spots](#known-fragile-spots)
- [Open items](#open-items)
- [Operational notes](#operational-notes)
- [Testing conventions](#testing-conventions)
- [Where things live](#where-things-live)
- [Getting oriented as a new contributor](#getting-oriented-as-a-new-contributor)

## What this document is for

`README.md` covers what LLMPivot does and how to run it. This document
covers *why* it's built the way it is — the reasoning behind decisions
that would otherwise look arbitrary, and the mistakes that shaped the
current design. Several things below look like they could be simplified
at a glance; this is where that impulse gets talked out of, or
confirmed.

## The six-class core

`ConfigManager`, `CandidateResolver`, `Router`, `FailureClassifier`,
`CooldownManager`, `GatewayClient`. The core was deliberately kept small
(roughly 600–1000 lines as a target, before the admin UI existed) and
split along these six responsibilities on purpose. `GatewayClient` is
the **only** class allowed to know LiteLLM exists — that boundary has
held since the beginning and is worth protecting, since it's what would
let the transport layer be swapped out later without touching routing
logic.

Candidates (a `Candidate` is one fully-resolved provider + model +
account tuple) are compiled once per resolve, rather than walked as a
live tree at request time. That compile step is what `CandidateResolver`
exists for, kept deliberately separate from `Router` — flattening the
provider/model/account tree once up front turned out to be the single
biggest simplification in the whole design.

## Non-obvious decisions and why

- **Per-request credential injection, not LiteLLM-side accounts.**
  LiteLLM's own `config.yaml` never lists individual API keys — the
  gateway injects `api_key` into the request body on every call. This is
  why LiteLLM's `model_list` entries need
  `configurable_clientside_auth_params: ["api_key"]`; without it, the
  override can fail silently and traffic falls back to LiteLLM's
  placeholder key. This has been confirmed working against real
  production traffic, not just in theory.
- **`CooldownManager` keys on `"{provider}:{account}"`, not
  `"{provider}:{account}:{model}"`.** Deliberate: an exhausted or banned
  account should be skipped for *all* of its models, not just the one
  that happened to fail first. Keying per-model was considered and
  rejected — don't revisit this without a good reason, since it was a
  considered tradeoff, not an oversight.
- **`MODEL_NOT_FOUND` (HTTP 404) never touches cooldown state.** A
  nonexistent or mistyped model name is an identity problem, not an
  account-health problem — penalizing the account for it was an actual
  bug, since fixed. It only counts against `routing.max_attempts` if the
  failed call took longer than `model_not_found_count_threshold_seconds`
  (default 2.0s): a fast rejection is treated as free, a slow one still
  costs attempt budget.
- **`router_settings.num_retries: 0` is mandatory in LiteLLM's own
  config, not optional.** Without it, LiteLLM's internal retry logic can
  silently drop the per-request `api_key` override on a retry attempt,
  producing a request that succeeds or fails at random on identical
  input. This was confirmed through live debugging and cost real time to
  track down — don't let it regress in any example LiteLLM config.
- **`ConfigWriter` serializes with `model_dump(exclude_defaults=True)`,
  not `exclude_none=True`.** `exclude_none` doesn't exclude `False`, so
  `include_remaining_models: false` was leaking into every endpoint
  entry whether or not it had actually been touched. Caught by a smoke
  test before it shipped.
- **`build_litellm_model()` avoids double-prefixing.** If a model string
  already includes the provider's `litellm_prefix` (e.g.
  `mistral/codestral-latest` for a provider whose prefix is already
  `mistral/`), naive string concatenation produces
  `mistral/mistral/codestral-latest`, which LiteLLM rejects. Fixed
  provider-agnostically rather than special-cased for one provider.
- **Provider entries within an endpoint stay grouped**, in the order
  they're written — there's no flat cross-provider interleaving of
  individual models. What looked at first like a need for interleaving
  turned out to already be possible at the data-model level, by
  repeating a provider entry multiple times within one endpoint's list.
- **`_parse_endpoint_form` uses submission/DOM order, not a numeric sort
  of field-name suffixes.** Field names carry a numeric suffix assigned
  at row-creation time, but the UI's reorder buttons move DOM nodes,
  which changes submission order without touching that suffix. Sorting
  numerically instead of trusting submission order would have silently
  discarded every reorder action — there's a regression test for this
  specifically
  (`test_parse_endpoint_form_uses_submission_order_not_numeric_index`).
- **Comments on models use a backward-compatible `str | ModelEntry`
  union**, rather than a schema-breaking change to `list[ModelEntry]`.
  Chosen so a hand-written `config.yaml` never breaks and stays
  diffable — uncommented entries still serialize as plain strings.
- **`include_remaining_models` resolves dynamically on every
  startup/reload**, and is never baked into a snapshot at save time.
  That's consistent with the project's general philosophy that
  wildcard/live-data resolution should always be fresh, never cached
  into the config file.
- **Account identity model:** `username` is the only free-text,
  never-sanitized, user-facing field. The internal account key
  (`config.yaml` dict key / URL segment / cooldown-state key) is a
  separate, stable, rarely-seen slug — overridable via an optional
  `label` field at creation, and renameable later via a dedicated action
  that also updates any endpoint's explicit account references. This
  went through a few iterations before landing here; collapsing username
  and account-key back into one field would undo a real usability fix.
- **Deleting an account actually removes its `.env` line and value.**
  Renaming the env var or rotating the key both leave the *old* line in
  place on purpose (non-destructive by default). Only delete is
  destructive — and both `.env` and `config.yaml` get a timestamped
  backup before every write regardless.

## Known-fragile spots

- A refactor once silently dropped a route's `@router.post` decorator on
  the `test_account` endpoint, and no pytest test caught it — only an
  ad-hoc smoke check did, and only because it happened to exercise that
  exact path. There's now a `test_all_expected_admin_routes_are_registered`
  test in `tests/test_admin_endpoint_form_parsing.py` that checks every
  admin route is actually registered. Keep this test current — add to
  its `expected` list whenever a new admin route is added, since it's
  the main guard against this failure mode recurring silently.
- Starlette's `TemplateResponse` signature has changed across versions
  (newer versions take `request` as an explicit first positional
  argument rather than folding it into a context dict). The project's
  `render()` helper centralizes this so only one place needs to know
  which calling convention is in effect — avoid calling
  `templates.TemplateResponse` directly in new routes; use `render()`
  instead.
- pytest auto-collects any imported function whose name starts with
  `test_` as a test case. `ai_gateway.admin.testing` exports
  `test_candidate`, `test_candidate_with_retry`, and `test_all_accounts`
  — these must always be imported under an alias in test files (see
  `tests/test_admin_testing.py`), never under their bare names, or
  pytest will try to run them as tests with fixture injection and fail
  in a confusing way.
- `cli.py`'s `--env-file` flag currently collides in name with `uv
  run`'s own `--env-file` flag — see the open item below.

## Open items

1. **`cli.py`'s `--env-file` flag name collides with `uv run
   --env-file`.** Running something like `uv run ai-gateway --data-dir
   .. --env-file ../.env --port 8000` can result in
   `AI_GATEWAY_DATA_DIR` never actually getting set the way you'd
   expect, since `uv run` intercepts its own `--env-file` flag first.
   Planned fix: rename the gateway's own flag (e.g. `--dotenv`), and
   have `main.py` log the *resolved absolute path* of the config/env
   files at startup, so this class of confusion is visible immediately
   rather than requiring a debugging session. Not yet implemented.
2. Some providers (GitHub Models among them) don't currently expand via
   LiteLLM's `check_provider_endpoint` wildcard discovery, and need
   their model list typed out by hand in `config.yaml` — same pattern as
   Groq, OpenRouter, and Mistral.
3. **Only Gemini has confirmed wildcard/model-discovery expansion** via
   LiteLLM's `check_provider_endpoint` as of this writing. Groq,
   OpenRouter, Mistral, and GitHub all currently require hand-typed
   exact model names in this project's `config.yaml`. This is a
   LiteLLM-side limitation rather than something this project's code can
   route around — check LiteLLM's current docs before assuming any given
   provider's status, since this list changes upstream over time.

## Operational notes

A typical setup runs LiteLLM in Docker (see
[`docs/docker.md`](docker.md)) and the gateway itself directly via `uv
run` on the host, with `config.yaml`/`.env` kept in a data directory
outside the repo checkout via `--data-dir` (see open item #1 above for
a rough edge in that flow). Real-world debugging has generally come from
pairing the gateway's own stdout with `docker logs` from the LiteLLM
container — several of the bugs listed above (the double-prefix issue,
the missing-decorator issue, the `num_retries` issue) were only found by
tracing through logs from an actual failing request, not from code
inspection alone. When something misbehaves, reaching for the logs
first is usually faster than reasoning about it from the code.

The admin UI has been used as a coding-assistant backend (via tools like
Roo Code and opencode) as much as it's been tested directly — worth
keeping in mind that a "stuck" symptom reported from a client tool can
sometimes trace back to the client's own local environment rather than
the gateway, so it's worth separating "gateway problem" from "client
tool problem" early when debugging anything reported from a downstream
tool.

## Testing conventions

- `tests/` uses pytest with `pytest-asyncio` (`asyncio_mode = "auto"` in
  `pyproject.toml`, so `@pytest.mark.asyncio` isn't required, though it's
  still present in most places for clarity).
- `pyproject.toml` requires Python `>=3.13`. If testing somewhere only
  Python 3.12 is available, temporarily relaxing that constraint locally
  works fine for running the suite — just make sure the constraint is
  back to `>=3.13` before anything gets packaged or shipped.
- Admin UI routes are covered both by pytest (`tests/test_admin_*.py`)
  and, for larger multi-file changes, by standalone smoke checks run
  outside pytest (using a `StubGatewayClient` alongside a real
  `TestClient`). The smoke checks have caught real HTTP-level wiring
  bugs that pytest's unit tests missed (the Starlette signature issue
  and the swallowed decorator, both mentioned above) — reach for one
  when a change touches several files at once, pytest alone otherwise.

## Where things live

```
src/ai_gateway/
  main.py              # FastAPI app, lifespan, _resolve_data_paths()
  cli.py                # `ai-gateway` console script (--data-dir etc.)
  candidates.py         # CandidateResolver, Candidate, build_litellm_model
  router.py             # Router (deterministic candidate iteration)
  cooldown.py            # CooldownManager
  failure.py             # FailureClassifier, FailureType
  gateway_client.py      # GatewayClient (only class that knows LiteLLM)
  reload.py              # shared reload_candidates(), used by /internal/reload and admin writes
  config/
    models.py            # Pydantic schema (GatewayConfig and everything under it)
    manager.py            # ConfigManager (load/validate, env var name resolution)
  api/
    routes.py             # /v1/chat/completions, /v1/models, /health, /internal/reload
  admin/
    routes.py              # all /admin/* routes (large file, ~800 lines)
    config_writer.py        # ConfigWriter (validated write + backup)
    env_file.py               # .env upsert/remove + backup
    testing.py                 # ad-hoc test-call logic, bypasses CooldownManager
    slugify.py                  # account key generation/sanitization
    provider_catalog.py          # curated provider suggestion list
    templates/                    # Jinja2, HTMX for test buttons, vanilla JS elsewhere
tests/                            # pytest suite, 190+ tests as of last count
config.example.yaml                # canonical example, kept in sync with schema
litellm-config.example.yaml         # companion file for the LiteLLM side (separate service)
config.schema.json                   # generated via scripts/generate_schema.py — regenerate after model changes
```

## Getting oriented as a new contributor

1. Start with `README.md` for the current-state architecture — this
   document assumes it rather than repeating it.
2. Skim "Non-obvious decisions" above before touching `candidates.py`,
   `cooldown.py`, or `admin/config_writer.py` specifically — that's
   where most of the decisions that look arbitrary actually live.
3. Run `uv run pytest` before making any change, to establish a clean
   baseline.
4. Check "Open items" above for anything already in progress before
   starting something that might overlap with it.
