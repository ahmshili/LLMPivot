"""Shared "config changed, make it live" logic, used by both
POST /internal/reload and every admin UI write, so there's exactly one
implementation of candidate recompilation rather than two that could drift.
"""

from __future__ import annotations

from ai_gateway.candidates import CandidateResolver
from ai_gateway.config.manager import ConfigManager
from ai_gateway.gateway_client import GatewayClient
from ai_gateway.router import Router


async def reload_candidates(
    config_manager: ConfigManager, gateway_client: GatewayClient, gateway_router: Router
) -> dict[str, int]:
    """Re-query LiteLLM's model list, recompile candidates from the
    current ConfigManager state, and swap them into the running Router.

    May raise ai_gateway.candidates.ModelValidationError if
    routing.strict_model_validation is enabled and a literal model wasn't
    found in LiteLLM's reported list.
    """
    available_models = await gateway_client.list_models()
    resolver = CandidateResolver(config_manager, available_models)
    candidates_by_endpoint = resolver.resolve()
    gateway_router.replace_candidates(candidates_by_endpoint)
    return {endpoint: len(candidates) for endpoint, candidates in candidates_by_endpoint.items()}
