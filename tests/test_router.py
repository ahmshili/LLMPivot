from __future__ import annotations

import asyncio

import pytest

from ai_gateway.candidates import Candidate
from ai_gateway.config.models import CooldownConfig, RoutingConfig
from ai_gateway.cooldown import AccountState, CooldownManager
from ai_gateway.failure import FailureClassifier, FailureType
from ai_gateway.gateway_client import GatewayResponse, OpenStreamResult
from ai_gateway.router import AllCandidatesFailedError, NoCandidatesAvailableError, Router


def make_candidate(provider: str, model: str, account: str, endpoint: str = "planner") -> Candidate:
    return Candidate(
        endpoint=endpoint,
        provider=provider,
        model=model,
        account=account,
        litellm_model=f"{provider}/{model}",
        api_key_env=f"{provider.upper()}_{account.upper()}_API_KEY",
        api_base=None,
    )


async def _bytes_iter(chunks: list[bytes]):
    for chunk in chunks:
        yield chunk


async def _noop_close() -> None:
    pass


def stream_result_from_response(response: GatewayResponse) -> OpenStreamResult:
    """Convenience: build the streaming-shaped equivalent of a
    GatewayResponse, so streaming tests can reuse the same scripted
    GatewayResponse fixtures as the non-streaming tests above.
    """
    if response.success:
        return OpenStreamResult(
            success=True,
            status_code=response.status_code,
            raw_text="",
            exception=None,
            body_iter=_bytes_iter([b"data: [DONE]\n\n"]),
            close=_noop_close,
        )
    return OpenStreamResult(
        success=False,
        status_code=response.status_code,
        raw_text=response.raw_text,
        exception=response.exception,
    )


class FakeGatewayClient:
    """Returns a scripted sequence of responses, one per call, in order.
    Optionally sleeps before returning each one, to simulate a fast vs slow
    failure for MODEL_NOT_FOUND accounting tests.
    """

    def __init__(self, responses: list[GatewayResponse], delays: list[float] | None = None) -> None:
        self._responses = list(responses)
        self._delays = list(delays) if delays is not None else [0.0] * len(responses)
        self.calls: list[Candidate] = []

    async def send_chat_completion(self, candidate, payload, *, timeout_seconds=None):
        self.calls.append(candidate)
        delay = self._delays.pop(0) if self._delays else 0.0
        if delay:
            await asyncio.sleep(delay)
        return self._responses.pop(0)

    async def open_chat_completion_stream(self, candidate, payload, *, timeout_seconds=None, read_timeout_seconds=None):
        self.calls.append(candidate)
        delay = self._delays.pop(0) if self._delays else 0.0
        if delay:
            await asyncio.sleep(delay)
        return stream_result_from_response(self._responses.pop(0))


def build_router(candidates, responses, max_attempts=8, delays=None, model_not_found_count_threshold_seconds=2.0):
    gateway_client = FakeGatewayClient(responses, delays=delays)
    cooldown_manager = CooldownManager(CooldownConfig())
    router = Router(
        candidates_by_endpoint={"planner": candidates},
        cooldown_manager=cooldown_manager,
        failure_classifier=FailureClassifier(),
        gateway_client=gateway_client,
        routing_config=RoutingConfig(
            max_attempts=max_attempts,
            request_timeout_seconds=5,
            model_not_found_count_threshold_seconds=model_not_found_count_threshold_seconds,
        ),
    )
    return router, gateway_client, cooldown_manager


@pytest.mark.asyncio
async def test_first_candidate_success() -> None:
    candidates = [make_candidate("gemini", "gemini-2.5-flash", "personal")]
    responses = [GatewayResponse(success=True, status_code=200, body={"ok": True}, raw_text="{}")]
    router, gateway_client, _ = build_router(candidates, responses)

    result = await router.route("planner", {"messages": []})
    assert result.response.body == {"ok": True}
    assert len(gateway_client.calls) == 1


@pytest.mark.asyncio
async def test_rotates_to_next_account_on_failure() -> None:
    candidates = [
        make_candidate("gemini", "gemini-2.5-flash", "personal"),
        make_candidate("gemini", "gemini-2.5-flash", "work"),
    ]
    responses = [
        GatewayResponse(success=False, status_code=429, body=None, raw_text="rate limited"),
        GatewayResponse(success=True, status_code=200, body={"ok": True}, raw_text="{}"),
    ]
    router, gateway_client, cooldown_manager = build_router(candidates, responses)

    result = await router.route("planner", {"messages": []})
    assert result.candidate.account == "work"
    assert len(gateway_client.calls) == 2
    assert cooldown_manager.is_available("gemini:personal") is False


@pytest.mark.asyncio
async def test_all_candidates_failed_raises() -> None:
    candidates = [
        make_candidate("gemini", "gemini-2.5-flash", "personal"),
        make_candidate("groq", "llama-4-scout", "primary"),
    ]
    responses = [
        GatewayResponse(success=False, status_code=500, body=None, raw_text="server error"),
        GatewayResponse(success=False, status_code=500, body=None, raw_text="server error"),
    ]
    router, _, _ = build_router(candidates, responses)

    with pytest.raises(AllCandidatesFailedError) as exc_info:
        await router.route("planner", {"messages": []})
    assert len(exc_info.value.attempts) == 2


@pytest.mark.asyncio
async def test_max_attempts_cap_is_respected() -> None:
    candidates = [
        make_candidate("gemini", "gemini-2.5-flash", "a"),
        make_candidate("gemini", "gemini-2.5-flash", "b"),
        make_candidate("gemini", "gemini-2.5-flash", "c"),
    ]
    responses = [
        GatewayResponse(success=False, status_code=500, body=None, raw_text="err"),
        GatewayResponse(success=False, status_code=500, body=None, raw_text="err"),
    ]
    router, gateway_client, _ = build_router(candidates, responses, max_attempts=2)

    with pytest.raises(AllCandidatesFailedError) as exc_info:
        await router.route("planner", {"messages": []})
    assert len(gateway_client.calls) == 2
    assert len(exc_info.value.attempts) == 2


@pytest.mark.asyncio
async def test_no_candidates_configured_raises() -> None:
    router, _, _ = build_router([], [])
    with pytest.raises(NoCandidatesAvailableError):
        await router.route("planner", {"messages": []})


@pytest.mark.asyncio
async def test_all_candidates_in_cooldown_raises_no_candidates_available() -> None:
    candidates = [make_candidate("gemini", "gemini-2.5-flash", "personal")]
    router, _, cooldown_manager = build_router(candidates, [])
    cooldown_manager.record_failure("gemini:personal", FailureType.AUTH_FAILURE)

    with pytest.raises(NoCandidatesAvailableError):
        await router.route("planner", {"messages": []})


@pytest.mark.asyncio
async def test_model_not_found_does_not_affect_cooldown_state() -> None:
    candidates = [
        make_candidate("gemini", "gemini-nonexistent-model", "personal"),
        make_candidate("gemini", "gemini-2.5-flash", "personal"),
    ]
    responses = [
        GatewayResponse(success=False, status_code=404, body=None, raw_text="model not found"),
        GatewayResponse(success=True, status_code=200, body={"ok": True}, raw_text="{}"),
    ]
    router, gateway_client, cooldown_manager = build_router(candidates, responses)

    result = await router.route("planner", {"messages": []})
    assert result.candidate.model == "gemini-2.5-flash"
    assert len(gateway_client.calls) == 2
    # The account was never penalized -- it should look exactly as if the
    # first (bad-model) call never happened, since it succeeded right after.
    assert cooldown_manager.get_state("gemini:personal") == AccountState.HEALTHY


@pytest.mark.asyncio
async def test_fast_model_not_found_does_not_count_against_max_attempts() -> None:
    candidates = [
        make_candidate("gemini", "gemini-bad-1", "personal"),
        make_candidate("gemini", "gemini-bad-2", "personal"),
        make_candidate("gemini", "gemini-2.5-flash", "personal"),
    ]
    responses = [
        GatewayResponse(success=False, status_code=404, body=None, raw_text="not found"),
        GatewayResponse(success=False, status_code=404, body=None, raw_text="not found"),
        GatewayResponse(success=True, status_code=200, body={"ok": True}, raw_text="{}"),
    ]
    # max_attempts=1, but both 404s are "fast" (no delay) and shouldn't
    # count, so the 3rd (real, successful) candidate should still be tried.
    router, gateway_client, _ = build_router(
        candidates, responses, max_attempts=1,
        model_not_found_count_threshold_seconds=1.0,
    )

    result = await router.route("planner", {"messages": []})
    assert result.candidate.model == "gemini-2.5-flash"
    assert len(gateway_client.calls) == 3


@pytest.mark.asyncio
async def test_slow_model_not_found_counts_against_max_attempts() -> None:
    candidates = [
        make_candidate("gemini", "gemini-bad-1", "personal"),
        make_candidate("gemini", "gemini-2.5-flash", "personal"),
    ]
    responses = [
        GatewayResponse(success=False, status_code=404, body=None, raw_text="not found"),
        GatewayResponse(success=True, status_code=200, body={"ok": True}, raw_text="{}"),
    ]
    # The 404 takes 50ms, well over a 10ms threshold, so it counts -- with
    # max_attempts=1, the router should stop before trying the 2nd (good)
    # candidate at all.
    router, gateway_client, _ = build_router(
        candidates, responses, max_attempts=1,
        delays=[0.05, 0.0],
        model_not_found_count_threshold_seconds=0.01,
    )

    with pytest.raises(AllCandidatesFailedError):
        await router.route("planner", {"messages": []})
    assert len(gateway_client.calls) == 1


@pytest.mark.asyncio
async def test_stream_first_candidate_success() -> None:
    candidates = [make_candidate("gemini", "gemini-2.5-flash", "personal")]
    responses = [GatewayResponse(success=True, status_code=200, body=None, raw_text="")]
    router, gateway_client, _ = build_router(candidates, responses)

    result = await router.route_stream_start("planner", {"messages": [], "stream": True})
    assert result.candidate.account == "personal"
    chunks = [c async for c in result.body_iter]
    assert chunks == [b"data: [DONE]\n\n"]
    await result.close()
    assert len(gateway_client.calls) == 1


@pytest.mark.asyncio
async def test_stream_rotates_to_next_account_on_failure() -> None:
    candidates = [
        make_candidate("gemini", "gemini-2.5-flash", "personal"),
        make_candidate("gemini", "gemini-2.5-flash", "work"),
    ]
    responses = [
        GatewayResponse(success=False, status_code=429, body=None, raw_text="rate limited"),
        GatewayResponse(success=True, status_code=200, body=None, raw_text=""),
    ]
    router, gateway_client, cooldown_manager = build_router(candidates, responses)

    result = await router.route_stream_start("planner", {"messages": [], "stream": True})
    assert result.candidate.account == "work"
    assert len(gateway_client.calls) == 2
    assert cooldown_manager.is_available("gemini:personal") is False
    await result.close()


@pytest.mark.asyncio
async def test_stream_all_candidates_failed_raises_before_any_forwarding() -> None:
    """Confirms the two-phase design: every candidate fails during the
    "open" phase, so the caller gets a clean exception -- never a
    partially-successful StreamRouteResult -- and no bytes were ever
    exposed to forward.
    """
    candidates = [
        make_candidate("gemini", "gemini-2.5-flash", "personal"),
        make_candidate("groq", "llama-4-scout", "primary"),
    ]
    responses = [
        GatewayResponse(success=False, status_code=500, body=None, raw_text="server error"),
        GatewayResponse(success=False, status_code=500, body=None, raw_text="server error"),
    ]
    router, _, _ = build_router(candidates, responses)

    with pytest.raises(AllCandidatesFailedError) as exc_info:
        await router.route_stream_start("planner", {"messages": [], "stream": True})
    assert len(exc_info.value.attempts) == 2


@pytest.mark.asyncio
async def test_stream_max_attempts_cap_is_respected() -> None:
    candidates = [
        make_candidate("gemini", "gemini-2.5-flash", "a"),
        make_candidate("gemini", "gemini-2.5-flash", "b"),
        make_candidate("gemini", "gemini-2.5-flash", "c"),
    ]
    responses = [
        GatewayResponse(success=False, status_code=500, body=None, raw_text="err"),
        GatewayResponse(success=False, status_code=500, body=None, raw_text="err"),
    ]
    router, gateway_client, _ = build_router(candidates, responses, max_attempts=2)

    with pytest.raises(AllCandidatesFailedError):
        await router.route_stream_start("planner", {"messages": [], "stream": True})
    assert len(gateway_client.calls) == 2


@pytest.mark.asyncio
async def test_stream_no_candidates_configured_raises() -> None:
    router, _, _ = build_router([], [])
    with pytest.raises(NoCandidatesAvailableError):
        await router.route_stream_start("planner", {"messages": [], "stream": True})


@pytest.mark.asyncio
async def test_stream_model_not_found_does_not_affect_cooldown_state() -> None:
    candidates = [
        make_candidate("gemini", "gemini-nonexistent-model", "personal"),
        make_candidate("gemini", "gemini-2.5-flash", "personal"),
    ]
    responses = [
        GatewayResponse(success=False, status_code=404, body=None, raw_text="model not found"),
        GatewayResponse(success=True, status_code=200, body=None, raw_text=""),
    ]
    router, gateway_client, cooldown_manager = build_router(candidates, responses)

    result = await router.route_stream_start("planner", {"messages": [], "stream": True})
    assert result.candidate.model == "gemini-2.5-flash"
    assert len(gateway_client.calls) == 2
    assert cooldown_manager.get_state("gemini:personal") == AccountState.HEALTHY
    await result.close()


@pytest.mark.asyncio
async def test_stream_success_records_cooldown_healthy() -> None:
    candidates = [make_candidate("gemini", "gemini-2.5-flash", "personal")]
    responses = [GatewayResponse(success=True, status_code=200, body=None, raw_text="")]
    router, _, cooldown_manager = build_router(candidates, responses)

    result = await router.route_stream_start("planner", {"messages": [], "stream": True})
    assert cooldown_manager.get_state("gemini:personal") == AccountState.HEALTHY
    await result.close()
