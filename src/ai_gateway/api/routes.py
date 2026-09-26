"""FastAPI routes.

Only the endpoints explicitly in scope are implemented:
    POST /v1/chat/completions
    GET  /v1/models
    GET  /health          (operational necessity, not in the original list,
                            implemented as a thin FastAPI route rather than
                            a GatewayClient method)
    POST /internal/reload (config hot-reload without restart)
PivotLLM -- https://github.com/ahmshili/LLMPivot -- Copyright (c) ahmshili.
Portfolio project, source-available license (see LICENSE at repo root):
view/evaluate only, no redistribution, no forks outside PRs to the
original repo, no production/commercial use without permission. This
notice must be preserved. Contact: a.shili.pers@gmail.com
"""

from __future__ import annotations

import logging

import httpx

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ai_gateway.api.schemas import (
    ChatCompletionRequest,
    ErrorDetail,
    ErrorResponse,
    ModelListEntry,
    ModelListResponse,
)
from ai_gateway.candidates import ModelValidationError
from ai_gateway.reload import reload_candidates
from ai_gateway.router import AllCandidatesFailedError, NoCandidatesAvailableError
from ai_gateway.security import require_api_auth

logger = logging.getLogger("ai_gateway.api")

router = APIRouter()


@router.post("/v1/chat/completions", dependencies=[Depends(require_api_auth)])
async def chat_completions(request: Request, body: ChatCompletionRequest):
    app_state = request.app.state
    gateway_router = app_state.router

    if not gateway_router.has_endpoint(body.model):
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=f"Unknown endpoint '{body.model}'.",
                    type="invalid_request_error",
                )
            ).model_dump(),
        )

    # Strip the client-facing virtual model name; GatewayClient injects the
    # real LiteLLM model name per attempt.
    payload = body.model_dump(exclude={"model"}, exclude_none=True)

    if body.stream:
        return await _stream_chat_completions(gateway_router, body.model, payload)

    try:
        result = await gateway_router.route(body.model, payload)
    except NoCandidatesAvailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(
                error=ErrorDetail(message=str(exc), type="no_candidates_available")
            ).model_dump(),
        ) from exc
    except AllCandidatesFailedError as exc:
        last_attempt = exc.attempts[-1]
        raise HTTPException(
            status_code=502,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=(
                        f"All {len(exc.attempts)} candidate(s) failed for "
                        f"endpoint '{body.model}'. Last error: "
                        f"{last_attempt.response.raw_text[:500]}"
                    ),
                    type="all_candidates_failed",
                )
            ).model_dump(),
        ) from exc

    return result.response.body


async def _stream_chat_completions(gateway_router, endpoint: str, payload: dict):
    """Handles the stream=true path. The entire candidate fallback loop
    (route_stream_start) runs to completion -- trying candidates until one
    succeeds or every candidate is exhausted -- BEFORE this function
    returns a StreamingResponse. This matters: once a StreamingResponse is
    constructed, Starlette sends response headers (with a fixed status
    code) to the client before ever asking its body iterator for a first
    chunk, so if fallback were attempted lazily inside that iterator, a
    failure after headers were already sent as "200 OK" could not be
    turned into a clean error response anymore. Resolving the candidate
    first keeps error handling identical to the non-streaming path: a
    real 502/503 JSON response if every candidate fails, never a
    half-sent stream.
    """
    try:
        stream_result = await gateway_router.route_stream_start(endpoint, payload)
    except NoCandidatesAvailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(
                error=ErrorDetail(message=str(exc), type="no_candidates_available")
            ).model_dump(),
        ) from exc
    except AllCandidatesFailedError as exc:
        last_attempt = exc.attempts[-1]
        raise HTTPException(
            status_code=502,
            detail=ErrorResponse(
                error=ErrorDetail(
                    message=(
                        f"All {len(exc.attempts)} candidate(s) failed for "
                        f"endpoint '{endpoint}'. Last error: "
                        f"{last_attempt.response.raw_text[:500]}"
                    ),
                    type="all_candidates_failed",
                )
            ).model_dump(),
        ) from exc

    async def forward():
        try:
            async for chunk in stream_result.body_iter:
                yield chunk
        except (httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.ReadError) as exc:
            # A mid-stream failure -- the candidate's response started
            # successfully and some (or all) content may already have
            # reached the client, so there's no safe way to fall back to
            # a different candidate at this point without producing a
            # broken/duplicated response (see route_stream_start's
            # docstring). The stream just ends here; the client sees a
            # truncated response, and this is deliberate, agreed
            # behavior -- not a bug to "fix" by adding fallback here.
            # What WAS a bug: letting this propagate uncaught crashed the
            # whole ASGI response with an ugly traceback instead of just
            # ending the stream, which is what actually happens now.
            logger.warning(
                "Streaming response for candidate %s ended early: %s: %s",
                stream_result.candidate,
                type(exc).__name__,
                exc,
            )
        finally:
            # Always release the upstream connection, whether forwarding
            # completed normally, the client disconnected early, or an
            # exception occurred while forwarding.
            await stream_result.close()

    return StreamingResponse(forward(), media_type="text/event-stream")


@router.get("/v1/models", dependencies=[Depends(require_api_auth)])
async def list_models(request: Request) -> ModelListResponse:
    gateway_router = request.app.state.router
    return ModelListResponse(
        data=[ModelListEntry(id=endpoint) for endpoint in gateway_router.endpoints()]
    )


@router.get("/health")
async def health(request: Request) -> dict:
    app_state = request.app.state
    litellm_reachable = await app_state.gateway_client.health_check()
    return {
        "litellm_reachable": litellm_reachable,
        "accounts": app_state.cooldown_manager.snapshot(),
        "endpoints": {
            endpoint: len(candidates)
            for endpoint, candidates in app_state.router.candidates_by_endpoint.items()
        },
    }


@router.post("/internal/reload", dependencies=[Depends(require_api_auth)])
async def reload_config(request: Request) -> dict:
    """Re-read the YAML config, recompile candidates, and swap them into
    the running Router. The existing CooldownManager instance is kept
    as-is, so cooldown/disabled state for accounts that still exist after
    the reload is preserved automatically; it is simply keyed by
    "provider:account" and untouched by this operation.
    """
    app_state = request.app.state
    try:
        config = app_state.config_manager.load()
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller deliberately
        logger.exception("Failed to reload configuration.")
        raise HTTPException(status_code=400, detail=f"Failed to reload configuration: {exc}") from exc

    try:
        endpoints_summary = await reload_candidates(app_state.config_manager, app_state.gateway_client, app_state.router)
    except ModelValidationError as exc:
        # Unlike startup, a bad reload should not take down an already
        # running gateway -- keep the previous candidates in effect.
        logger.error("Reload rejected due to strict_model_validation failure:\n%s", exc)
        raise HTTPException(status_code=400, detail=f"Reload rejected: {exc}") from exc

    logger.info("Configuration reloaded successfully.")
    return {
        "status": "reloaded",
        "endpoints": endpoints_summary,
        "provider_count": len(config.providers),
    }
