"""Curated list of providers commonly used for free-tier LLM access, shown
as a filterable suggestion list in the admin UI's provider-name field.
This is deliberately not the full ~100-provider list LiteLLM supports
(pulling that in would mean depending on the litellm package itself); any
provider name not on this list can still be typed in manually -- the
field is a suggestion, not an enforced enum.

litellm_prefix values are best-effort based on LiteLLM's naming
conventions at time of writing -- verify against LiteLLM's current provider
docs before relying on one for a less common entry. Getting one wrong
just means that provider resolves zero live candidates (already handled
gracefully elsewhere in this project), not a crash.

Free-tier terms (limits, whether a card is required, whether it's a
permanent tier vs. a one-time trial credit) change often and are not
tracked here -- verify current terms directly with each provider before
relying on one.
"""

from __future__ import annotations

CURATED_PROVIDERS: list[dict[str, str]] = [
    {"name": "gemini", "litellm_prefix": "gemini/"},
    {"name": "groq", "litellm_prefix": "groq/"},
    {"name": "openrouter", "litellm_prefix": "openrouter/"},
    {"name": "mistral", "litellm_prefix": "mistral/"},
    {"name": "cerebras", "litellm_prefix": "cerebras/"},
    {"name": "github", "litellm_prefix": "github/"},
    {"name": "deepseek", "litellm_prefix": "deepseek/"},
    {"name": "nvidia_nim", "litellm_prefix": "nvidia_nim/"},
    {"name": "cohere", "litellm_prefix": "cohere/"},
    {"name": "huggingface", "litellm_prefix": "huggingface/"},
    {"name": "cloudflare", "litellm_prefix": "cloudflare/"},
    {"name": "sambanova", "litellm_prefix": "sambanova/"},
    {"name": "zhipuai", "litellm_prefix": "zhipuai/"},
    {"name": "ollama", "litellm_prefix": "ollama/"},
    {"name": "dashscope", "litellm_prefix": "dashscope/"},
    {"name": "together_ai", "litellm_prefix": "together_ai/"},
    {"name": "fireworks_ai", "litellm_prefix": "fireworks_ai/"},
    {"name": "anyscale", "litellm_prefix": "anyscale/"},
    {"name": "perplexity", "litellm_prefix": "perplexity/"},
    {"name": "xai", "litellm_prefix": "xai/"},
    {"name": "voyage", "litellm_prefix": "voyage/"},
    {"name": "replicate", "litellm_prefix": "replicate/"},
    {"name": "anthropic", "litellm_prefix": "anthropic/"},
    {"name": "azure_ai", "litellm_prefix": "azure_ai/"},
    {"name": "vertex_ai", "litellm_prefix": "vertex_ai/"},
    {"name": "watsonx", "litellm_prefix": "watsonx/"},
    {"name": "moonshot", "litellm_prefix": "moonshot/"},
    {"name": "novita", "litellm_prefix": "novita/"},
    {"name": "baseten", "litellm_prefix": "baseten/"},
    {"name": "modal", "litellm_prefix": "modal/"},
]
