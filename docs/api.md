Part of the [LLMPivot](../README.md) documentation.

# API reference

## Table of contents

- [POST /v1/chat/completions](#post-v1chatcompletions)
- [GET /v1/models](#get-v1models)
- [GET /health](#get-health)
- [POST /internal/reload](#post-internalreload)

## POST /v1/chat/completions

`model` selects an endpoint (e.g. `"planner"`), not a real provider
model. Streaming (`stream: true`) is **not supported in v1** and
returns `400`; failing over mid-stream is a fundamentally different
problem than failing over before the first byte.

## GET /v1/models

Returns the configured **endpoints** (virtual models), not underlying
provider model names.

## GET /health

LiteLLM reachability plus a snapshot of every account's cooldown/disabled
state.

## POST /internal/reload

Re-reads the YAML config and recompiles candidates without a restart.
Cooldown/disabled state for accounts that still exist afterward is
preserved (it lives in `CooldownManager`, keyed by `provider:account`,
independent of the candidate list).

## GET /

Redirects (302) to `/admin/` when the admin UI is reachable. In prod mode without `--enable-admin`
the admin is hidden, so it returns a small JSON pointer to `/docs` and `/health` instead. `HEAD /` is
handled too, so PaaS health probes get a clean response.
