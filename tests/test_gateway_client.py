from __future__ import annotations

import httpx
import pytest

from ai_gateway.candidates import Candidate
from ai_gateway.config.models import LiteLLMConfig
from ai_gateway.gateway_client import GatewayClient


def make_client(config: LiteLLMConfig, handler) -> GatewayClient:
    """Build a GatewayClient whose underlying httpx.AsyncClient is wired to
    a MockTransport, so we can inspect exactly what was sent without a real
    LiteLLM instance.
    """
    client = GatewayClient(config)
    client._client = httpx.AsyncClient(
        base_url=config.base_url,
        timeout=config.timeout_seconds,
        transport=httpx.MockTransport(handler),
    )
    return client


@pytest.mark.asyncio
async def test_no_auth_header_when_proxy_api_key_env_unset() -> None:
    seen_headers: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(200, json={"data": [{"id": "gemini/gemini-2.5-flash"}]})

    config = LiteLLMConfig(base_url="http://litellm.test", proxy_api_key_env=None)
    client = make_client(config, handler)

    await client.list_models()
    assert "authorization" not in seen_headers


@pytest.mark.asyncio
async def test_auth_header_sent_when_proxy_api_key_env_set(monkeypatch) -> None:
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test-master-key")
    seen_headers: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(200, json={"data": []})

    config = LiteLLMConfig(base_url="http://litellm.test", proxy_api_key_env="LITELLM_MASTER_KEY")
    client = make_client(config, handler)

    await client.list_models()
    assert seen_headers.get("authorization") == "Bearer sk-test-master-key"


@pytest.mark.asyncio
async def test_missing_proxy_key_env_var_sends_no_header(monkeypatch, caplog) -> None:
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    seen_headers: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(200, json={"data": []})

    config = LiteLLMConfig(base_url="http://litellm.test", proxy_api_key_env="LITELLM_MASTER_KEY")
    client = make_client(config, handler)

    await client.list_models()
    assert "authorization" not in seen_headers


@pytest.mark.asyncio
async def test_list_models_raises_on_401() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    config = LiteLLMConfig(base_url="http://litellm.test")
    client = make_client(config, handler)

    with pytest.raises(httpx.HTTPStatusError):
        await client.list_models()


def make_candidate() -> Candidate:
    return Candidate(
        endpoint="test",
        provider="gemini",
        model="gemini-2.5-flash",
        account="personal",
        litellm_model="gemini/gemini-2.5-flash",
        api_key_env="GEMINI_PERSONAL_API_KEY",
        api_base=None,
    )


@pytest.mark.asyncio
async def test_open_stream_success_forwards_bytes(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_PERSONAL_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
        )

    config = LiteLLMConfig(base_url="http://litellm.test")
    client = make_client(config, handler)

    result = await client.open_chat_completion_stream(make_candidate(), {"messages": [], "stream": True})
    assert result.success is True
    assert result.status_code == 200

    chunks = [chunk async for chunk in result.body_iter]
    assert b"".join(chunks) == b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'
    await result.close()


@pytest.mark.asyncio
async def test_open_stream_failure_reads_error_body_and_does_not_return_body_iter(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_PERSONAL_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    config = LiteLLMConfig(base_url="http://litellm.test")
    client = make_client(config, handler)

    result = await client.open_chat_completion_stream(make_candidate(), {"messages": [], "stream": True})
    assert result.success is False
    assert result.status_code == 429
    assert "rate limited" in result.raw_text
    assert result.body_iter is None
    assert result.close is None


@pytest.mark.asyncio
async def test_open_stream_missing_api_key_env_returns_failure_without_network_call(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_PERSONAL_API_KEY", raising=False)
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    config = LiteLLMConfig(base_url="http://litellm.test")
    client = make_client(config, handler)

    result = await client.open_chat_completion_stream(make_candidate(), {"messages": [], "stream": True})
    assert result.success is False
    assert result.exception is not None
    assert called is False


@pytest.mark.asyncio
async def test_open_stream_injects_model_and_api_key(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_PERSONAL_API_KEY", "test-key")
    seen_body: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen_body.update(json.loads(request.content))
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    config = LiteLLMConfig(base_url="http://litellm.test")
    client = make_client(config, handler)

    result = await client.open_chat_completion_stream(make_candidate(), {"messages": [], "stream": True})
    await result.close()

    assert seen_body["model"] == "gemini/gemini-2.5-flash"
    assert seen_body["api_key"] == "test-key"
    assert seen_body["stream"] is True


@pytest.mark.asyncio
async def test_open_stream_uses_separate_read_timeout(monkeypatch) -> None:
    """Regression test for a real bug: a single shared timeout value was
    applied to every httpx timeout category, including the per-chunk
    read timeout, which killed healthy long-running streams (opencode
    reported streaming "timing out" for legitimately long generations).
    Connect/write/pool should use the short request timeout; read should
    use the separate, more generous stream read timeout.
    """
    monkeypatch.setenv("GEMINI_PERSONAL_API_KEY", "test-key")
    captured_timeout = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    config = LiteLLMConfig(base_url="http://litellm.test")
    client = make_client(config, handler)

    # Patch AsyncClient.stream to capture the timeout it's called with,
    # without needing to reach into httpx's internals for the real request.
    original_stream = client._client.stream

    def spying_stream(*args, **kwargs):
        captured_timeout["timeout"] = kwargs.get("timeout")
        return original_stream(*args, **kwargs)

    client._client.stream = spying_stream

    result = await client.open_chat_completion_stream(
        make_candidate(),
        {"messages": [], "stream": True},
        timeout_seconds=30.0,
        read_timeout_seconds=300.0,
    )
    await result.close()

    timeout = captured_timeout["timeout"]
    assert timeout.connect == 30.0
    assert timeout.read == 300.0
    assert timeout.write == 30.0
    assert timeout.pool == 30.0
