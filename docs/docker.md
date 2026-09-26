Part of the [LLMPivot](../README.md) documentation.

# Running with Docker

## Table of contents

- [LiteLLM (the transport layer)](#litellm-the-transport-layer)
- [Running LiteLLM and LLMPivot together with Compose](#running-litellm-and-llmpivot-together-with-compose)
- [Why there's no single combined container](#why-theres-no-single-combined-container)

All Docker-related files live in [`docker/`](../docker/): a `Dockerfile`
for LLMPivot itself, and a `docker-compose.yml` that wires it up
alongside LiteLLM.

## LiteLLM (the transport layer)

LLMPivot expects a LiteLLM instance to already be running as a plain
OpenAI-compatible transport — see [`docs/configuration.md`](configuration.md)
for what its config needs to contain (`litellm-config.example.yaml` at
the repo root is a ready-to-adapt starting point). The simplest way to
run just that piece is LiteLLM's official image directly:

```bash
docker run -d \
  --name litellm \
  -p 4000:4000 \
  -v $(pwd)/litellm-config.example.yaml:/app/config.yaml \
  -e GEMINI_API_KEY=placeholder \
  ghcr.io/berriai/litellm:main-stable \
  --config /app/config.yaml --port 4000
```

A few notes on that command:

- `-v` mounts your adapted LiteLLM config into the container.
- The `GEMINI_API_KEY` (and equivalents for other providers) only needs
  to be a placeholder value — real per-account credentials are injected
  per request by LLMPivot itself (see `docs/configuration.md`), not read
  from this environment variable at request time.
- Point LLMPivot's `config.yaml` → `litellm.base_url` at
  `http://localhost:4000` if you're running LLMPivot directly on the
  host (e.g. via `uv run ai-gateway`), or at the service name if both
  are on the same Docker network — see below.

## Running LiteLLM and LLMPivot together with Compose

For running both services together, [`docker/docker-compose.yml`](../docker/docker-compose.yml)
builds LLMPivot from [`docker/Dockerfile`](../docker/Dockerfile) and
starts it alongside LiteLLM on a shared network, so neither service
needs `host.docker.internal` or a hardcoded IP to reach the other.

From the repo root:

```bash
# Put your real config.yaml and .env in docker/data/ first —
# that's the directory the LLMPivot container reads from.
mkdir -p docker/data
cp config.example.yaml docker/data/config.yaml
cp .env.example docker/data/.env
# then edit docker/data/config.yaml and docker/data/.env with real values

docker compose -f docker/docker-compose.yml up -d
```

LLMPivot will be reachable at `http://localhost:8000`, and LiteLLM at
`http://localhost:4000`. Logs for either service:

```bash
docker compose -f docker/docker-compose.yml logs -f llmpivot
docker compose -f docker/docker-compose.yml logs -f litellm
```

## Why there's no single combined container

It might seem convenient to bundle LiteLLM and LLMPivot into one image
and run them as a single container, but that goes against the standard
one-process-per-container convention Docker is built around: the two
services would share a lifecycle (a crash or restart in one takes down
the other), share logs (much harder to tell which service produced a
given line), and lose the ability to scale, update, or restart either
one independently. The two-container Compose setup above achieves the
same "one command to start everything" convenience without those
downsides, so that's the supported path here.
