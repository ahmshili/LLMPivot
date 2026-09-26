# API reference

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
