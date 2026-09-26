Part of the [LLMPivot](../README.md) documentation.

# Routing and failure handling

## Table of contents

- [How routing works](#how-routing-works)
- [Filtering non-chat model variants out of a wildcard](#filtering-non-chat-model-variants-out-of-a-wildcard)
- [Wildcard model discovery — provider support varies](#wildcard-model-discovery--provider-support-varies)

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

## Filtering non-chat model variants out of a wildcard

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

## Wildcard model discovery — provider support varies

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
