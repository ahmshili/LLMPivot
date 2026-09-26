Part of the [LLMPivot](../README.md) documentation.

# Walkthrough: from install to a working AI assistant

## Table of contents

- [1. Run the app](#1-run-the-app)
- [2. Add a provider, models, and an account](#2-add-a-provider-models-and-an-account)
- [3. Create a virtual endpoint](#3-create-a-virtual-endpoint)
- [4. A realistic configuration, end to end](#4-a-realistic-configuration-end-to-end)
- [5. Connect an AI assistant](#5-connect-an-ai-assistant)
- [What happens when a model hits its limit](#what-happens-when-a-model-hits-its-limit)

A full step-by-step tour of setting up LLMPivot, configuring providers
and accounts through the admin UI, and pointing a real AI coding
assistant ([opencode](https://opencode.ai)) at it as a drop-in
OpenAI-compatible endpoint.

## 1. Run the app

`uv` downloads all dependencies automatically on first run. If you
haven't installed `uv` (or a LiteLLM instance to route through) yet,
see [`docs/installation.md`](installation.md) first.

```bash
cd LLMPivot
uv run ai-gateway --port 8000
```

![Startup logs](../images/2026-09-23-06-09-42.png)
![Startup complete](../images/2026-09-23-06-11-11.png)

Once startup finishes, the admin UI is reachable at:

```
http://127.0.0.1:8000/admin/
```

![Admin dashboard](../images/2026-09-23-06-29-24.png)

**Provider list**
![Provider list](../images/2026-09-23-06-12-54.png)

**Endpoint list**
![Endpoint list](../images/2026-09-24-04-03-03.png)

## 2. Add a provider, models, and an account

Providers are added through the graphical interface:

![Adding a provider](../images/2026-09-23-06-13-41.png)

Then add the model list. (In this example, an invalid provider prefix
means LiteLLM's live default model list wasn't detected — in normal use
this step auto-suggests from LiteLLM's `/v1/models`.)

![Adding models](../images/2026-09-24-04-09-49.png)

Finish setting up the provider by adding its first account:

![Adding an account](../images/2026-09-24-04-11-29.png)

## 3. Create a virtual endpoint

A virtual endpoint points at one provider, or an ordered list of
providers and models — this is what becomes your OpenAI-compatible
`model` value.

![Endpoint editing](../images/2026-09-24-04-13-54.png)

Back in `/admin/`, the new virtual endpoint appears as your gateway's
OpenAI-compatible target — the same way you'd point an assistant at an
Ollama endpoint.

## 4. A realistic configuration, end to end

A preview of what a real, filled-in configuration looks like. Make sure
your own setup stays within your providers' usage policies (see the
[disclaimer](../README.md#usage-disclaimer) in the main README).

**Provider list**
![Provider list example](../images/2026-09-23-06-17-19.png)

**Account list**
![Account list example](../images/2026-09-24-04-30-40.png)

**Endpoint list**
![Endpoint list example 1](../images/2026-09-24-04-33-38.png)
![Endpoint list example 2](../images/2026-09-24-04-33-58.png)
![Endpoint list example 3](../images/2026-09-24-04-35-14.png)

## 5. Connect an AI assistant

Using [opencode](https://opencode.ai) as an example. First, set up the
endpoint:

![opencode endpoint setup](../images/2026-09-24-04-39-29.png)

Configure it like this:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "local-proxy/fast",
  "provider": {
    "local-proxy": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Local Multi-Provider Proxy",
      "options": {
        "baseURL": "http://127.0.0.1:8000/v1",
        "streaming": false
      },
      "models": {
        "fast": { "name": "Fast Model" },
        "test": { "name": "Test Model" }
      }
    }
  }
}
```

![Config applied](../images/2026-09-24-04-42-16.png)

Launch the assistant:

```bash
opencode
```

![Launching opencode](../images/2026-09-24-04-44-50.png)

Run `/models` and confirm the `/test` endpoint is selected:

![Model selection](../images/2026-09-24-04-45-35.png)

Send a simple prompt:

![Prompt example](../images/2026-09-24-04-48-30.png)

The gateway's own logs confirm the custom endpoint was used:

![Gateway logs](../images/2026-09-24-04-49-41.png)

## What happens when a model hits its limit

When a priority model hits a quota limit or becomes unavailable,
LLMPivot automatically switches to the next model on the list — no
manual intervention needed. This is what makes it practical to run an
AI assistant entirely on free-tier LLM APIs.

**Note:** streaming responses are also supported when enabled in the AI
assistant's settings.

Here is an example of a fallback chain from the gateway's Logs 
![Gateway logs](../images/2026-09-24-06-24-01.png)