Part of the [LLMPivot](../README.md) documentation.

# Live demo (Render)

A public read-only instance runs at **https://llmpivot.onrender.com** (replace with the real URL).
It runs in **demo mode**, so no LiteLLM backend and no real API keys are involved.

## What a visitor can do

- Open the admin UI (the root `/` redirects to `/admin/`) and browse providers, accounts, models and endpoints.
- Press the **Test** / **Test all** buttons. In demo mode they return mock responses.
- Call the API. `/v1/models` and `/health` work, and `/v1/chat/completions` returns mock completions.

## What a visitor cannot do

Every other admin action (add, edit, delete, reorder, backup, rotate key) is rejected with a
`403` and a "read-only public demo" message. Nothing is ever written to the config. A banner at the
top of every admin page says so.

## Cold starts

Free Render instances sleep after roughly 15 minutes without traffic. The first request after that
can take about a minute while the instance boots. If you want it always warm, point a free monitor
(for example UptimeRobot) at `/health` every 10 minutes. This is optional.

## How it is deployed

| Setting | Value |
| --- | --- |
| Build command | `uv sync` |
| Start command | `uv run ai-gateway --host 0.0.0.0 --port $PORT` |
| Health check path | `/health` |
| Env var | `DEMO_MODE=true` |
| Config | A `config.yaml` supplied as a Render secret file and pointed to by `AI_GATEWAY_CONFIG` (it is git-ignored, so it is not in the repository) |

Render redeploys on every push. The filesystem is ephemeral, so any local state resets on each
redeploy or restart.

## Why `--prod` is not used

`--prod --enable-admin` protects the admin UI with a bearer token. Browsers cannot send that header
on a normal page load, so the UI would answer 401 to visitors. Demo mode makes the admin read-only
instead, which is the right shape for a public showcase. For a real deployment, use `--prod` and
call the admin from an API client, or put it behind a reverse proxy that handles login.

## Security notes for this deployment

- No real secrets: the config uses placeholder keys, and demo mode never contacts a provider.
- The API is intentionally open: it can only return mock responses, so it costs nothing to abuse.
- Do not paste real keys into a demo instance.
