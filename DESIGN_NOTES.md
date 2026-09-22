# Design notes — AI Gateway

This is **not** user-facing documentation (that's `README.md`). This is
the reasoning trail behind the non-obvious decisions in the codebase —
kept so that anyone (including future-me) picking this project back up
doesn't accidentally "simplify" away something that was deliberate.
Several things below look arbitrary at a glance; this document says why
they aren't.

## What this project is

A lightweight OpenAI-compatible gateway sitting in front of LiteLLM,
purpose-built for orchestrating **free-tier LLM APIs** across many
providers and many accounts per provider. LiteLLM is the transport layer
only (provider SDK translation); this gateway owns config, routing,
retries, cooldowns, and account rotation. Full architecture is in
`README.md` — this document is about *why* it's built the way it is, not
*what* it does.

## The six-class core (do not casually restructure this)

`ConfigManager`, `CandidateResolver`, `Router`, `FailureClassifier`,
`CooldownManager`, `GatewayClient`. This was the explicitly approved
architecture from the original design conversation, chosen deliberately
small (~600–1000 line target for the core, before the admin UI existed).
`GatewayClient` is the **only** class allowed to know LiteLLM exists —
this boundary has held throughout and should keep holding.

Candidates (a `Candidate` = one fully-resolved provider+model+account
tuple) are compiled once per resolve, not walked as a live tree at
request time. This was called "the single biggest simplification" in the
original design and is why `CandidateResolver` exists as a separate
compile step from `Router`.

## Non-obvious decisions and why (the part a README can't carry)

- **Per-request credential injection (Mode C), not LiteLLM-side accounts.**
  LiteLLM's `config.yaml` never lists individual API keys. The gateway
  injects `api_key` in the request body per call. This is why LiteLLM's
  `model_list` entries need `configurable_clientside_auth_params:
  ["api_key"]` — without it, the override can silently fail and traffic
  falls back to LiteLLM's placeholder key. **Confirmed working in
  production traffic**, not just in theory.
- **`CooldownManager` keys on `"{provider}:{account}"`, not
  `"{provider}:{account}:{model}"`.** Deliberate: an exhausted/banned
  account should be skipped for *all* its models, not just the one that
  failed. This was explicitly requested and confirmed by the user, who at
  one point asked for per-model keying and was talked out of it — don't
  "fix" this back to per-model without re-confirming.
- **`MODEL_NOT_FOUND` (HTTP 404) never touches cooldown state.** A
  nonexistent/typo'd model is an identity problem, not an account-health
  problem — penalizing the account for it was a real bug, fixed. It also
  only counts against `routing.max_attempts` if the failed call took
  longer than `model_not_found_count_threshold_seconds` (default 2.0s) —
  a fast rejection is free, a slow one still costs budget.
- **`router_settings.num_retries: 0` is mandatory in LiteLLM's config,
  not optional.** Confirmed via live debugging: without it, LiteLLM's own
  internal retry can silently drop the per-request `api_key` override on
  a retry attempt, producing a request that succeeds or fails at random
  on identical input. This cost significant debugging time — don't let
  this regress in any generated LiteLLM config example.
- **`ConfigWriter` serializes with `model_dump(exclude_defaults=True)`,
  not `exclude_none=True`.** Real bug fix: `exclude_none` doesn't exclude
  `False`, so `include_remaining_models: false` was leaking into every
  endpoint entry regardless of whether the user touched it. Caught by a
  smoke test before shipping, not by code review.
- **`build_litellm_model()` avoids double-prefixing.** If a user types or
  pastes a model string that already includes the provider's
  `litellm_prefix` (e.g. `mistral/codestral-latest` for a provider whose
  prefix is already `mistral/`), naive concatenation produces
  `mistral/mistral/codestral-latest`, which LiteLLM rejects. Fixed
  provider-agnostically, not just for Mistral.
- **Provider-entries in an endpoint stay grouped**, in the order written;
  no flat cross-provider interleaving of individual models. The user
  explicitly confirmed this is fine — what looked like a request for
  interleaving turned out to already work at the data-model level
  (repeat a provider entry multiple times in one endpoint's list).
- **`_parse_endpoint_form` uses submission/DOM order, not a numeric sort
  of field-name suffixes.** This was a real bug during the endpoint
  provider-entry reordering feature: field names carry a numeric suffix
  assigned at row-creation time, but the reorder buttons move DOM nodes,
  which changes submission order, not the suffix. Sorting numerically
  would have silently discarded every reorder action. There's a
  regression test for this specifically
  (`test_parse_endpoint_form_uses_submission_order_not_numeric_index`).
- **Comments on models use a backward-compatible `str | ModelEntry`
  union**, not a schema-breaking change to `list[ModelEntry]`. Chosen
  specifically so hand-written `config.yaml` never breaks and stays
  diffable — uncommented entries always serialize as plain strings.
- **`include_remaining_models` resolves dynamically on every
  startup/reload**, never baked into a snapshot at save time. This was an
  explicit design choice (option B over option A) matching the project's
  existing philosophy that wildcards/live-data resolution should always
  be fresh, never cached into the config file.
- **Account identity model**: `username` is the only free-text,
  never-sanitized, user-facing field. The internal account key
  (config.yaml dict key / URL segment / cooldown-state key) is a
  separate, stable, rarely-seen slug — overridable via an optional
  `label` field at creation, renameable later via a dedicated action that
  also updates any endpoint's explicit account references. This went
  through several iterations before landing here; don't collapse
  username and account-key back into one field.
- **Deleting an account actually removes its `.env` line and value.**
  Renaming the env var or rotating the key both leave the *old* line
  orphaned on purpose (non-destructive). Only delete is destructive, and
  both `.env` and `config.yaml` get a timestamped backup before every
  write regardless.

## Known-fragile spots / things that have already broken once

- **A `str_replace` edit once silently swallowed a route's `@router.post`
  decorator** (`test_account`), and no pytest test caught it — only an
  ad-hoc smoke script did, and only because it happened to exercise that
  exact path. There's now a `test_all_expected_admin_routes_are_registered`
  test in `tests/test_admin_endpoint_form_parsing.py` that checks every
  admin route is registered — keep this test, and add to its `expected`
  list whenever a new admin route is added, since it's the main guard
  against this exact failure mode recurring silently.
- **Starlette's `TemplateResponse` signature changed** (newer versions
  take `request` as an explicit first positional arg, not a context dict
  key). All templates go through a local `render()` wrapper in
  `admin/routes.py` specifically to insulate against this — don't call
  `templates.TemplateResponse` directly in new routes, use `render()`.
- **pytest auto-collects any imported `test_*`-prefixed function as a
  test case.** `ai_gateway.admin.testing` exports `test_candidate`,
  `test_candidate_with_retry`, `test_all_accounts` — these must always be
  imported with aliases in test files (see
  `tests/test_admin_testing.py`), never as bare names, or pytest will try
  to run them as tests with fixture injection and fail confusingly.
- **`cli.py`'s `--env-file` flag collided with `uv run`'s own
  `--env-file` flag** — found live, not yet fixed as of this document
  (see Outstanding Issues below).

## Outstanding issues (as of this document)

1. **`cli.py`'s `--env-file` flag name collides with `uv run --env-file`.**
   User ran `uv run ai-gateway --data-dir .. --env-file ../.env --port
   8000` and `AI_GATEWAY_DATA_DIR` never actually got set (confirmed via
   the startup log showing a relative `config.yaml` path, not an
   absolute one under the data dir). Planned fix: rename the flag (e.g.
   `--dotenv`), and make `main.py` log the *resolved absolute path* of
   config/env files at startup so this class of confusion is visible
   immediately rather than requiring debugging. Not yet implemented —
   was in "asking mode" confirming the fix, not yet built, when this
   handoff was written.
2. **GitHub Models provider has no `defaults.models` configured at all**
   in the user's live config — this is a user config gap, not a code
   bug. GitHub Models is (like Groq/OpenRouter/Mistral) apparently not on
   LiteLLM's `check_provider_endpoint` expansion support list, so it
   needs exact model names typed by hand, same pattern as the others.
   User was in the middle of clarifying an observation about GitHub
   "querying alright" when manually set — worth re-reading that exchange
   before assuming resolved.
3. **Only Gemini has confirmed wildcard/model-discovery expansion** via
   LiteLLM's `check_provider_endpoint`. Groq, OpenRouter, Mistral, and
   GitHub all require hand-typed exact model names in this gateway's
   `config.yaml`. This is a LiteLLM-side limitation, not something this
   project's code can route around — confirm current LiteLLM docs before
   assuming any specific provider's status, since this list can change
   upstream.

## User's environment and workflow (operational, not architectural)

- Runs everything via Docker for LiteLLM, with this gateway run directly
  via `uv run` (not containerized) from `~/projects/ai-stack/ai-gateway/`.
- Data directory intent: `config.yaml` and `.env` should live in the
  **parent** `ai-stack/` directory, not inside `ai-gateway/` — this is
  what `--data-dir` was built for (see Outstanding Issue #1 for why it's
  not fully working yet).
- Current provider roster: Gemini (5 accounts), Groq (1), OpenRouter (1),
  Mistral (2), GitHub (1). Real account usernames are personal email
  addresses — treat any pasted config/env content as sensitive, don't
  echo API key values back in explanations unless specifically relevant
  to debugging that exact value.
- User explicitly wants **token-conservative** interaction from here:
  favor pytest (fast, cheap, already comprehensive — 116+ tests) over
  building new large ad-hoc smoke-test scripts for incremental changes.
  Reserve full smoke-test batteries for genuinely large/risky multi-file
  changes, not routine fixes.
- User tests primarily through the admin UI directly against their real
  LiteLLM/provider setup, not just automated tests — expect bug reports
  to come with real logs from `docker logs litellm` and the gateway's own
  stdout, often pasted together. Read logs closely; several real bugs in
  this project were only found by tracing exact log lines the user
  pasted (the double-prefix bug, the missing-decorator bug, the
  `num_retries` bug were all found this way, not by inspection alone).
- User uses Roo Code / opencode (VS Code extensions) as gateway clients.
  A prior "stuck at API request" report turned out to be conflated with
  an unrelated Roo Code `ripgrep`-not-found issue in the extension host
  logs — worth separating "gateway problem" from "client tool's own
  local environment problem" carefully when debugging client-side
  symptoms, since the two can appear in the same pasted log.

## Testing conventions

- `tests/` uses pytest + `pytest-asyncio` (`asyncio_mode = "auto"` in
  `pyproject.toml`, no `@pytest.mark.asyncio` needed but present anyway
  in most places for clarity).
- Sandbox note (if working in a similarly constrained environment):
  `pyproject.toml` requires Python `>=3.13`, but sandboxes may only have
  3.12 available. The pattern used throughout this project's development
  was: temporarily relax to `>=3.12`, install/test, then restore to
  `>=3.13` before packaging the final deliverable. Don't ship an archive
  with the relaxed constraint.
- Admin UI routes are tested both via pytest (`tests/test_admin_*.py`)
  and, for larger multi-file batches, via standalone smoke-test scripts
  run outside pytest (using a `StubGatewayClient` and a real
  `TestClient`) — these catch real HTTP-level wiring bugs pytest unit
  tests miss (e.g. the Starlette signature issue, the swallowed
  decorator). Given the token-conservation note above, default to pytest
  only unless the change is large/risky.

## Where things live (quick map)

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
tests/                            # pytest suite, 116+ tests as of last count
config.example.yaml                # canonical example, kept in sync with schema
litellm-config.example.yaml         # companion file for the LiteLLM side (separate service)
config.schema.json                   # generated via scripts/generate_schema.py — regenerate after model changes
```

## If you're picking this up fresh: suggested first steps

1. Read `README.md` for current-state architecture (this document assumes
   it, doesn't repeat it).
2. Skim "Non-obvious decisions" above before touching `candidates.py`,
   `cooldown.py`, or `admin/config_writer.py` specifically — that's where
   most of the deliberate-looking-arbitrary decisions live.
3. Run `uv run pytest` before making any change, to have a clean
   baseline.
4. Check "Outstanding issues" above for what was mid-flight when this
   document was written.
