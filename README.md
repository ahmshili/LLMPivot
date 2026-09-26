<p align="center">
  <img src="images/banner.png" alt="LLMPivot banner" width="100%">
</p>

<p align="center">
  <a href="docs/walkthrough.md">Walkthrough</a> ·
  <a href="docs/README.md">Docs</a> ·
  <a href="docs/design-notes.md">Design Notes</a> ·
  <a href="LICENSE">License</a>
</p>

<p align="center">
  <a href="https://github.com/ahmshili/LLMPivot/actions/workflows/tests.yml"><img src="https://github.com/ahmshili/LLMPivot/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Personal%20Use%20Only-lightgrey.svg" alt="License: Personal Use Only"></a>
  <img src="https://img.shields.io/badge/python-3.13%2B-blue" alt="Python 3.13+">
  <img src="https://img.shields.io/badge/framework-FastAPI-009688" alt="FastAPI">
  <img src="https://img.shields.io/badge/tested%20with-pytest-0A9EDC" alt="Tested with pytest">
  <a href="#sponsorship"><img src="https://img.shields.io/badge/sponsor-%E2%9D%A4-ea4aaa" alt="Sponsor this project"></a>
</p>

# LLMPivot

**A self-healing API gateway for LLMs.** It sits in front of multiple AI
providers (Gemini, Groq, OpenRouter, Mistral, GitHub Models, ...) and
exposes them as a single, standard OpenAI-compatible endpoint. If one
provider, model, or account fails or runs out of quota, LLMPivot
automatically retries the next one in line — no dropped requests, no
manual account juggling.

Built to solve a real, everyday problem: running LLM-powered apps
cheaply across several providers' free tiers means constantly watching
quotas and swapping keys by hand. LLMPivot automates that entirely,
with a web dashboard to manage everything.

## Table of contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Quickstart](#quickstart)
- [A look at the admin dashboard](#a-look-at-the-admin-dashboard)
- [Documentation](#documentation)
- [Development](#development)
- [Why it's built this way](#why-its-built-this-way)
- [Usage disclaimer](#usage-disclaimer)
- [Issues, feedback & contributions](#issues-feedback--contributions)
- [Sponsorship](#sponsorship)
- [License](#license)

## What it does

- **One endpoint, many providers.** Any tool that speaks the OpenAI API
  (chat apps, coding assistants, internal scripts) can point at
  LLMPivot and get automatic failover across providers and accounts,
  with zero code changes on the client side.
- **Smart, not just "retry on error."** Failures are classified (bad
  key, quota exhausted, model typo, network blip, etc.) and handled
  differently — a bad API key is disabled immediately, a rate limit
  backs off and retries, a quota hit waits it out. See
  [`docs/routing.md`](docs/routing.md) for the full logic.
- **Web admin dashboard.** Add providers, rotate API keys, reorder
  fallback priority, and test accounts live — all through a UI, no
  manual YAML editing required. See [`docs/admin-ui.md`](docs/admin-ui.md).
- **Config-as-code, safely.** Settings are validated against a schema
  generated straight from the code, so a broken config can't ship.
  Secrets are always kept out of version control.

## Architecture

```
OpenAI-compatible clients
                              │
                              ▼
                     LLMPivot (this project)
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

LLMPivot doesn't replace [LiteLLM](https://github.com/BerriAI/litellm) —
LiteLLM stays the transport layer that talks to each provider's SDK.
LLMPivot owns everything around that: configuration, account pools,
routing order, failure handling, and retries.

## Tech stack

Python · FastAPI · Pydantic (schema-validated YAML config) · Jinja2 +
HTMX (server-rendered admin UI) · pytest (190+ tests, ~4,000 lines of
application code).

## Quickstart

**Prerequisites:** Python 3.13+, [`uv`](https://docs.astral.sh/uv/getting-started/installation/), and Docker (for LiteLLM). One-liner to get `uv` itself:

| OS | Install command |
|---|---|
| macOS / Linux | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Windows (PowerShell) | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |

`uv` manages the correct Python version for you — no separate Python install needed. Installing Docker itself, and other install methods per OS, are covered in [`docs/installation.md`](docs/installation.md).

```bash
# 1. Start LiteLLM (the transport layer LLMPivot routes through)
docker run -d --name litellm -p 4000:4000 \
  -v $(pwd)/litellm-config.example.yaml:/app/config.yaml \
  -e GEMINI_API_KEY=placeholder \
  ghcr.io/berriai/litellm:main-stable \
  --config /app/config.yaml --port 4000

# 2. Configure LLMPivot
cp config.example.yaml config.yaml
cp .env.example .env        # fill in your provider API keys

# 3. Run
uv sync
uv run ai-gateway --port 8000
```

Then send a standard OpenAI-style request to `http://localhost:8000/v1/chat/completions`,
or open `http://localhost:8000/admin/` to manage providers and accounts visually.

That `docker run` command is enough to get started; for running both
services together with Docker Compose (recommended once you're past
the first test), see [`docs/docker.md`](docs/docker.md). Full
LiteLLM-side config, environment variables, and securing the gateway are
in [`docs/configuration.md`](docs/configuration.md).

## A look at the admin dashboard

<p align="center">
  <img src="images/2026-09-23-06-29-24.png" alt="LLMPivot admin dashboard" width="85%">
</p>

<p align="center">
  <img src="images/2026-09-24-04-33-38.png" alt="Editing a virtual endpoint's routing order" width="85%">
</p>

A complete, step-by-step walkthrough — installing, adding providers and
accounts, building a virtual endpoint, and pointing a real AI assistant
([opencode](https://opencode.ai)) at LLMPivot as a drop-in OpenAI
endpoint — is in [`docs/walkthrough.md`](docs/walkthrough.md).

## Documentation

- [`docs/walkthrough.md`](docs/walkthrough.md) — full install-to-first-request walkthrough with screenshots
- [`docs/installation.md`](docs/installation.md) — installing Python/uv and LiteLLM prerequisites on Linux, macOS, and Windows
- [`docs/routing.md`](docs/routing.md) — how requests are routed and retried, failure types, cooldown rules
- [`docs/configuration.md`](docs/configuration.md) — full config reference, LiteLLM setup, securing the gateway
- [`docs/admin-ui.md`](docs/admin-ui.md) — every admin UI feature in detail
- [`docs/api.md`](docs/api.md) — API endpoint reference
- [`docs/docker.md`](docs/docker.md) — running LiteLLM and LLMPivot with Docker, including the two-container Compose setup
- [`docs/design-notes.md`](docs/design-notes.md) — engineering decision log: why things were built this way, bugs found along the way, and tradeoffs made deliberately

## Development

```bash
uv sync --extra dev
uv run pytest
```

Tests run automatically on every push via GitHub Actions (see the
"Tests" badge above).

## Why it's built this way

No microservices, no database, no message queue, no background workers —
just six small, single-responsibility components
(`ConfigManager`, `CandidateResolver`, `Router`, `FailureClassifier`,
`CooldownManager`, `GatewayClient`), each easy to reason about in
isolation. `GatewayClient` is the only class that knows LiteLLM exists,
so swapping the transport later only touches one file. More on these
tradeoffs in [`docs/design-notes.md`](docs/design-notes.md).

## Usage disclaimer

LLMPivot is a routing and orchestration tool — it does not grant access
to any LLM provider, and it does not change the terms you agreed to when
you signed up for one. **Any use of this project must comply with the
usage policy, rate limits, and terms of service of each LLM provider you
connect it to.** You are solely responsible for how you configure and
use your own provider accounts, including any consequences from
combining or rotating across free-tier accounts. The author accepts no
liability for how the software is used — see [LICENSE](LICENSE) for the
full terms.

## Issues, feedback & contributions

Bug reports, feature suggestions, and general feedback are very welcome
via [GitHub Issues](https://github.com/ahmshili/LLMPivot/issues) — that's
the best place to flag something broken, propose an improvement, or ask
a question about the design.

Pull requests are welcome too, with one condition tied to this project's
license (see below): contributions should be submitted directly against
this repository rather than developed in a separate fork maintained
long-term elsewhere. Before opening a larger PR, opening an issue first
to discuss the approach is appreciated, but not required for small
fixes.

## Sponsorship

If LLMPivot saves you time or money on LLM API costs, sponsorships are
welcome and appreciated — they help justify continued maintenance and
new features. Sponsorship links will be added here once set up; in the
meantime, a ⭐ on the repo or a mention if you're using it in your own
project both go a long way.

## License

LLMPivot is **source-available for personal, non-commercial use only** —
this is **not** an MIT/OSI-style open-source license. In short:

- ✅ Personal, non-commercial use is permitted
- ✅ Modifications may be contributed back via pull request to this repository
- ❌ Commercial use is not permitted
- ❌ Redistribution, or maintaining a separate fork/copy elsewhere, is not permitted
- ❌ No warranty is provided, and the author bears no liability for how you use it (including any consequences with your LLM providers — see the disclaimer above)
- Any mention or showcase of this project must credit and link back to [this repository](https://github.com/ahmshili/LLMPivot)

Full terms are in [`LICENSE`](LICENSE). This is not legal advice — if
you need certainty about a specific use case, consult a lawyer.
