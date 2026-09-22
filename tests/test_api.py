from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_gateway.api import router as api_router
from ai_gateway.candidates import Candidate
from ai_gateway.config.models import CooldownConfig, RoutingConfig
from ai_gateway.cooldown import CooldownManager
from ai_gateway.failure import FailureClassifier
from ai_gateway.gateway_client import GatewayResponse, OpenStreamResult
from ai_gateway.router import Router


class StubGatewayClient:
    def __init__(self, response: GatewayResponse, stream_result: OpenStreamResult | None = None) -> None:
        self._response = response
        # Defaults to a stream that mirrors the non-streaming response's
        # success/failure, so most tests don't need to specify both.
        self._stream_result = stream_result or OpenStreamResult(
            success=response.success,
            status_code=response.status_code,
            raw_text=response.raw_text,
            exception=response.exception,
            body_iter=_bytes_iter([b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n', b"data: [DONE]\n\n"])
            if response.success
            else None,
            close=_noop_close if response.success else None,
        )
        self.health_ok = True

    async def send_chat_completion(self, candidate, payload, *, timeout_seconds=None):
        return self._response

    async def open_chat_completion_stream(self, candidate, payload, *, timeout_seconds=None, read_timeout_seconds=None):
        return self._stream_result

    async def health_check(self) -> bool:
        return self.health_ok


async def _bytes_iter(chunks: list[bytes]):
    for chunk in chunks:
        yield chunk


async def _noop_close() -> None:
    pass


def build_app(
    response: GatewayResponse, stream_result: OpenStreamResult | None = None
) -> tuple[FastAPI, StubGatewayClient]:
    app = FastAPI()
    app.include_router(api_router)

    candidate = Candidate(
        endpoint="planner",
        provider="gemini",
        model="gemini-2.5-flash",
        account="personal",
        litellm_model="gemini/gemini-2.5-flash",
        api_key_env="GEMINI_PERSONAL_API_KEY",
        api_base=None,
    )
    gateway_client = StubGatewayClient(response, stream_result)
    cooldown_manager = CooldownManager(CooldownConfig())
    router = Router(
        candidates_by_endpoint={"planner": [candidate]},
        cooldown_manager=cooldown_manager,
        failure_classifier=FailureClassifier(),
        gateway_client=gateway_client,
        routing_config=RoutingConfig(),
    )

    app.state.router = router
    app.state.gateway_client = gateway_client
    app.state.cooldown_manager = cooldown_manager
    return app, gateway_client


def test_chat_completions_success() -> None:
    response_body = {"id": "chatcmpl-1", "choices": [{"message": {"role": "assistant", "content": "hi"}}]}
    app, _ = build_app(
        GatewayResponse(success=True, status_code=200, body=response_body, raw_text="{}")
    )
    client = TestClient(app)

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "planner", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert resp.status_code == 200
    assert resp.json() == response_body


def test_chat_completions_unknown_endpoint() -> None:
    app, _ = build_app(GatewayResponse(success=True, status_code=200, body={}, raw_text="{}"))
    client = TestClient(app)

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "not-an-endpoint", "messages": []},
    )
    assert resp.status_code == 404


def test_chat_completions_streaming_success_forwards_bytes() -> None:
    app, _ = build_app(GatewayResponse(success=True, status_code=200, body={}, raw_text="{}"))
    client = TestClient(app)

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "planner", "messages": [{"role": "user", "content": "hi"}], "stream": True},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert b"data: [DONE]" in resp.content


def test_chat_completions_streaming_all_failed_returns_502_not_a_broken_stream() -> None:
    """Every candidate fails before any bytes are sent to the client --
    this must come back as a normal JSON 502, never as a 200 with a
    truncated/empty stream. This is the exact scenario the two-phase
    open-then-commit design exists to make possible.
    """
    failing_stream = OpenStreamResult(
        success=False, status_code=500, raw_text="server error", exception=None
    )
    app, _ = build_app(
        GatewayResponse(success=False, status_code=500, body=None, raw_text="server error"),
        stream_result=failing_stream,
    )
    client = TestClient(app)

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "planner", "messages": [], "stream": True},
    )
    assert resp.status_code == 502
    assert resp.headers["content-type"].startswith("application/json")


def test_chat_completions_streaming_records_success_in_cooldown_manager() -> None:
    app, gateway_client = build_app(GatewayResponse(success=True, status_code=200, body={}, raw_text="{}"))
    client = TestClient(app)

    client.post(
        "/v1/chat/completions",
        json={"model": "planner", "messages": [], "stream": True},
    )

    snapshot = app.state.cooldown_manager.snapshot()
    assert snapshot["gemini:personal"]["state"] == "healthy"


def test_chat_completions_streaming_failure_records_cooldown() -> None:
    failing_stream = OpenStreamResult(
        success=False, status_code=429, raw_text="rate limited", exception=None
    )
    app, _ = build_app(
        GatewayResponse(success=False, status_code=429, body=None, raw_text="rate limited"),
        stream_result=failing_stream,
    )
    client = TestClient(app)

    client.post(
        "/v1/chat/completions",
        json={"model": "planner", "messages": [], "stream": True},
    )

    snapshot = app.state.cooldown_manager.snapshot()
    assert snapshot["gemini:personal"]["state"] == "cooldown"


def test_chat_completions_streaming_mid_stream_read_timeout_ends_cleanly_not_a_crash() -> None:
    """Regression test for a real bug: a candidate's response starts
    successfully (headers/status 200 arrive fine), then the connection
    fails partway through forwarding (httpx.ReadTimeout, e.g. a long gap
    between chunks during a legitimate long generation, or the upstream
    connection dropping). This must end the stream cleanly -- the ASGI
    app must NOT crash with an unhandled exception, which is exactly
    what happened before this was fixed (visible as "ERROR: Exception in
    ASGI application" plus a full traceback in the user's own logs, and
    reported as opencode's requests "timing out").
    """
    closed = False

    async def _raises_mid_stream():
        yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        raise httpx.ReadTimeout("timed out waiting for next chunk", request=None)

    async def _close():
        nonlocal closed
        closed = True

    stream_result = OpenStreamResult(
        success=True,
        status_code=200,
        raw_text="",
        exception=None,
        body_iter=_raises_mid_stream(),
        close=_close,
    )
    app, _ = build_app(
        GatewayResponse(success=True, status_code=200, body={}, raw_text="{}"),
        stream_result=stream_result,
    )
    client = TestClient(app)

    # Must not raise -- this is the actual regression: the exception used
    # to propagate all the way out and crash the ASGI response handling.
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "planner", "messages": [], "stream": True},
    )
    assert resp.status_code == 200
    assert b"partial" in resp.content
    assert closed is True


def test_chat_completions_all_failed_returns_502() -> None:
    app, _ = build_app(
        GatewayResponse(success=False, status_code=500, body=None, raw_text="server error")
    )
    client = TestClient(app)

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "planner", "messages": []},
    )
    assert resp.status_code == 502


def test_list_models_returns_endpoints() -> None:
    app, _ = build_app(GatewayResponse(success=True, status_code=200, body={}, raw_text="{}"))
    client = TestClient(app)

    resp = client.get("/v1/models")
    assert resp.status_code == 200
    ids = {entry["id"] for entry in resp.json()["data"]}
    assert ids == {"planner"}


def test_health_endpoint() -> None:
    app, gateway_client = build_app(GatewayResponse(success=True, status_code=200, body={}, raw_text="{}"))
    client = TestClient(app)

    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["litellm_reachable"] is True
    assert body["endpoints"] == {"planner": 1}
