"""GatewayClient: the only component in this project that knows LiteLLM
exists.

No other class imports httpx or references a LiteLLM URL. If LiteLLM is
ever replaced with a different transport, this is the only file that
should need to change.

Per-request credential injection is used deliberately: LiteLLM's own
config never lists individual accounts, so there is exactly one source of
truth for accounts (this gateway's YAML config), never two.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

from ai_gateway.candidates import Candidate
from ai_gateway.config.models import LiteLLMConfig

logger = logging.getLogger("ai_gateway.gateway_client")


@dataclass
class GatewayResponse:
    """Uniform result of a single attempt against LiteLLM.

    GatewayClient never raises for ordinary HTTP or network failures --
    it converts them into this value object so FailureClassifier and
    Router can inspect a plain struct rather than catching exceptions
    scattered across layers.
    """

    success: bool
    status_code: int | None
    body: dict[str, Any] | None
    raw_text: str
    exception: Exception | None = None


@dataclass
class OpenStreamResult:
    """Result of opening a streaming chat completion attempt.

    Two-phase by design: headers (and therefore status_code) arrive
    before any body bytes are read, so the caller (Router) can decide
    whether to commit to forwarding this candidate's stream to the
    client, or abandon it and fall back to the next candidate -- exactly
    the same failure-then-fallback shape as the non-streaming path, just
    gated on "did this candidate's response start successfully" instead
    of "did this candidate's whole response succeed".

    On success, `body_iter` yields raw bytes exactly as LiteLLM sent them
    (no re-parsing/re-serializing of the SSE framing -- a pure passthrough
    is the least likely thing to subtly break some client's SSE parser).
    `close` MUST be called exactly once when the caller is done with this
    attempt, whether or not `body_iter` was ever consumed (failure case,
    early abandonment, client disconnect, or normal completion) -- it
    releases the underlying HTTP connection.
    """

    success: bool
    status_code: int | None
    raw_text: str
    exception: Exception | None
    body_iter: Any | None = None  # AsyncIterator[bytes], only set when success
    close: Any | None = None  # Callable[[], Awaitable[None]], only set when success


class GatewayClient:
    """Sends chat completion requests to LiteLLM and lists available models.

    Exposes four operations:
        send_chat_completion()
        open_chat_completion_stream()
        list_models()
        health_check()
    """

    def __init__(self, config: LiteLLMConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(base_url=config.base_url, timeout=config.timeout_seconds)

    def _auth_headers(self) -> dict[str, str]:
        """Build the Authorization header for calls to the LiteLLM proxy
        itself, if proxy-side auth is configured. This is deliberately
        separate from per-account provider API keys: this header
        authenticates the gateway to LiteLLM; the per-request `api_key`
        field in the body authenticates LiteLLM to the upstream provider.
        """
        if not self._config.proxy_api_key_env:
            return {}
        proxy_key = os.environ.get(self._config.proxy_api_key_env)
        if not proxy_key:
            logger.warning(
                "litellm.proxy_api_key_env is set to '%s' but that "
                "environment variable is empty; calls to LiteLLM will be "
                "sent without an Authorization header and will likely be "
                "rejected with 401 if the proxy requires auth.",
                self._config.proxy_api_key_env,
            )
            return {}
        return {"Authorization": f"Bearer {proxy_key}"}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send_chat_completion(
        self,
        candidate: Candidate,
        payload: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> GatewayResponse:
        """Send a single chat completion attempt for one candidate.

        The candidate's real LiteLLM model name and per-account API key are
        injected here; the caller's payload should already have any
        client-facing virtual model name stripped out.
        """
        api_key = os.environ.get(candidate.api_key_env)
        if not api_key:
            # Should not normally happen -- CandidateResolver excludes
            # candidates with missing keys -- but guard defensively in case
            # an env var was unset after startup.
            return GatewayResponse(
                success=False,
                status_code=None,
                body=None,
                raw_text="",
                exception=RuntimeError(
                    f"Environment variable '{candidate.api_key_env}' is not set."
                ),
            )

        body = dict(payload)
        body["model"] = candidate.litellm_model
        body["api_key"] = api_key
        if candidate.api_base:
            body["api_base"] = candidate.api_base

        try:
            response = await self._client.post(
                "/v1/chat/completions",
                json=body,
                headers=self._auth_headers(),
                timeout=timeout_seconds or self._config.timeout_seconds,
            )
        except httpx.RequestError as exc:
            logger.warning("Network error calling LiteLLM for %s: %s", candidate, exc)
            return GatewayResponse(success=False, status_code=None, body=None, raw_text="", exception=exc)

        raw_text = response.text
        parsed_body: dict[str, Any] | None
        try:
            parsed_body = response.json()
        except ValueError:
            parsed_body = None

        return GatewayResponse(
            success=response.is_success,
            status_code=response.status_code,
            body=parsed_body,
            raw_text=raw_text,
        )

    async def open_chat_completion_stream(
        self,
        candidate: Candidate,
        payload: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        read_timeout_seconds: float | None = None,
    ) -> OpenStreamResult:
        """Open a streaming chat completion attempt for one candidate.

        Mirrors send_chat_completion's credential injection exactly, but
        returns as soon as response headers arrive rather than waiting for
        the full body -- see OpenStreamResult's docstring for why.

        `timeout_seconds` governs connect/write/pool -- how long to wait
        to establish the connection and send the request, same meaning as
        the non-streaming path, so a genuinely unreachable provider still
        fails fast. `read_timeout_seconds` is deliberately separate: it
        governs the gap between individual received chunks, not total
        response time, and needs to be much more generous for a
        long-running generation with natural pauses between tokens. A
        single shared timeout value here was a real bug -- httpx applies
        one float to every timeout category unless told otherwise, so
        reusing the short request timeout as a read timeout killed
        healthy, still-generating streams whenever two chunks were more
        than ~30s apart.

        On failure, the (usually small, non-streamed) error body is read
        immediately and the connection is closed before returning, so the
        caller gets a plain, already-complete GatewayResponse-shaped
        result it can classify exactly like the non-streaming path -- no
        different failure-handling logic needed for streaming vs not.
        """
        api_key = os.environ.get(candidate.api_key_env)
        if not api_key:
            return OpenStreamResult(
                success=False,
                status_code=None,
                raw_text="",
                exception=RuntimeError(f"Environment variable '{candidate.api_key_env}' is not set."),
            )

        body = dict(payload)
        body["model"] = candidate.litellm_model
        body["api_key"] = api_key
        if candidate.api_base:
            body["api_base"] = candidate.api_base

        connect_timeout = timeout_seconds or self._config.timeout_seconds
        timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout_seconds or connect_timeout,
            write=connect_timeout,
            pool=connect_timeout,
        )

        stream_ctx = self._client.stream(
            "POST",
            "/v1/chat/completions",
            json=body,
            headers=self._auth_headers(),
            timeout=timeout,
        )
        try:
            response = await stream_ctx.__aenter__()
        except httpx.RequestError as exc:
            logger.warning("Network error opening stream to LiteLLM for %s: %s", candidate, exc)
            return OpenStreamResult(success=False, status_code=None, raw_text="", exception=exc)

        if not response.is_success:
            raw_bytes = await response.aread()
            await stream_ctx.__aexit__(None, None, None)
            return OpenStreamResult(
                success=False,
                status_code=response.status_code,
                raw_text=raw_bytes.decode("utf-8", errors="replace"),
                exception=None,
            )

        async def _close() -> None:
            await stream_ctx.__aexit__(None, None, None)

        return OpenStreamResult(
            success=True,
            status_code=response.status_code,
            raw_text="",
            exception=None,
            body_iter=response.aiter_bytes(),
            close=_close,
        )

    async def list_models(self) -> list[str]:
        """Query LiteLLM's /v1/models and return the raw list of model IDs.

        Called once at startup (and again on /internal/reload) to resolve
        wildcards against what LiteLLM actually has configured. This call
        intentionally sends proxy auth headers (if configured) and does
        NOT swallow errors: a 401 here means the gateway itself is
        misconfigured against LiteLLM, and should fail startup loudly
        rather than surface later as every account being disabled.
        """
        response = await self._client.get("/v1/models", headers=self._auth_headers())
        response.raise_for_status()
        data = response.json()
        return [item["id"] for item in data.get("data", []) if "id" in item]

    async def health_check(self) -> bool:
        """Return True if LiteLLM is reachable."""
        try:
            response = await self._client.get("/v1/models", headers=self._auth_headers())
        except httpx.RequestError:
            return False
        return response.is_success
