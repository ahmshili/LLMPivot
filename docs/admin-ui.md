Part of the [LLMPivot](../README.md) documentation.

# Admin UI

## Table of contents

- [Overview](#overview)
- [Providers](#providers)
- [Accounts](#accounts)
- [Test / Test all accounts](#test--test-all-accounts)
- [Models](#models)
- [Endpoints](#endpoints)
- [How writes are applied](#how-writes-are-applied)
- [Troubleshooting: a vague test failure](#troubleshooting-a-vague-test-failure)
- [Environment variables](#environment-variables)

## Overview

A local, server-rendered admin UI is available at `/admin/` for managing
providers, accounts, priority models, and endpoints without hand-editing
`config.yaml` or `.env`. A few conveniences worth calling out
specifically: accounts are **reorderable** (the same up/down/top/bottom
controls used for endpoint entries) and their internal key/label is
**renameable** separately from username (endpoint references to a
renamed account update automatically); an optional **label** field on
add/edit overrides the auto-generated key outright (e.g. `personal`
instead of an auto-slugified `jane_doe`); a **show/hide API
keys** toggle sits on the accounts table (hidden by default, with no
layout shift when toggled); and a **"Create backup now"** button on the
dashboard takes an on-demand snapshot of both `config.yaml` and `.env`
together, on top of the automatic backup already made before every
write.

**This UI is unauthenticated by design**, matching this project's
trusted-local-network assumption -- do not expose it beyond localhost or
a trusted network.

## Providers

Add/edit/delete, with a curated suggestion list for common free-tier
providers (Gemini, Groq, OpenRouter, Mistral, Cerebras, GitHub Models,
Cloudflare Workers AI, SambaNova, Z.ai/GLM, Ollama Cloud, Qwen, ...) plus
free-text entry for anything else, and an optional comment.

## Accounts

Add/edit/delete per provider. **`username` is the only identity you ever
need to think about** -- free text, shown exactly as typed everywhere
(an email address, a nickname, whatever), and freely editable later.
Everything else about an account's identity is generated and hidden: a
stable internal key (used only as the `config.yaml` dict key and admin
URL segment) and an env var name, both derived once at creation and
never something you need to manage. Also editable per account,
independently: the **API key** (rotate -- overwrites the same `.env`
value, takes effect immediately) and the **env var name** itself
(rename -- copies the value to a new `.env` line, old line left in
place unused), plus a free-text comment.

## Test / Test all accounts

Fires real call(s) through the same `GatewayClient`/`FailureClassifier`
path production traffic uses -- deliberately bypassing `CooldownManager`,
since a manual test must never mark an account healthy or disabled. A
failed test gets **one automatic retry**, but only for
`TRANSIENT`/`NETWORK_FAILURE` outcomes (never `AUTH_FAILURE`,
`MODEL_NOT_FOUND`, or `QUOTA_EXHAUSTED`, which retrying can't change).
"Test all accounts" for a provider is paced by `admin.test_concurrency`
/ `admin.test_delay_seconds` in `config.yaml` (default: fully
sequential, 0.3s apart) specifically so a bulk test run can't itself
trip a free tier's per-minute limit -- raise `test_concurrency` only
once you know your accounts' actual limits.

## Models

A priority-ordered list per provider (add via a filterable text input
suggesting LiteLLM's live reported models, freeform entry still allowed
for wildcards), reorderable, each with an optional comment, plus the
exclude-patterns list. The wildcard registration string itself (e.g. a
bare `*` once LiteLLM's own `gemini/*` deployment entry is
prefix-stripped) is filtered out of every suggestion/selection list --
it was never a valid model to select or test against.

## Endpoints

Add/edit/delete, with an optional comment. Provider entries are
**reorderable** (up/down, move to top/bottom) and the same provider can
appear more than once in one endpoint -- e.g. `gemini` -> `groq` ->
`gemini` again with different models, tried in exactly that order. Each
entry always has its own **custom ordered model list** (never an
implicit "provider defaults" mode): toggling off "use this provider's
default priority models" seeds the list from the provider's current
default order as a one-time starting point, then it's freely editable
and reorderable from there, same picker style as the provider's own
model editor. An optional **"include remaining models"** checkbox per
entry appends every other live model for that provider (exclude-filtered)
as low-priority fallback -- resolved fresh at every startup/reload,
never baked into a snapshot, and stored as a single
`include_remaining_models: true` flag that's simply omitted from
`config.yaml` when left off, so it doesn't clutter entries that don't
use it. Providers/models/accounts are all selected via filter+bulk-select
checklists or the ordered picker -- never free text -- which is what
keeps this safe at high account counts. Duplicate provider entries get
a small "(#2)" label so it's clear at a glance which is which.

## How writes are applied

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

## Troubleshooting: a vague test failure

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

## Environment variables

- `AI_GATEWAY_ENV_FILE` -- which dotenv file the admin UI upserts secrets
  into (default `.env`). This is separate from whatever your launcher
  used to populate the process's initial environment (e.g. `uv run
  --env-file ../.env`), since the running process has no way to know that
  path on its own. **Keep these two in sync** -- see above.
