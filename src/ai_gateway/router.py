"""Router: deterministic iteration over a precomputed candidate list.

Because candidates are fully resolved ahead of time by CandidateResolver,
the router itself contains no knowledge of providers, models, or accounts
as separate concepts -- it only knows how to walk a list, skip
unavailable entries, and stop.
PivotLLM -- https://github.com/ahmshili/LLMPivot -- Copyright (c) ahmshili.
Portfolio project, source-available license (see LICENSE at repo root):
view/evaluate only, no redistribution, no forks outside PRs to the
original repo, no production/commercial use without permission. This
notice must be preserved. Contact: a.shili.pers@gmail.com
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from ai_gateway.candidates import Candidate
from ai_gateway.config.models import RoutingConfig
from ai_gateway.cooldown import CooldownManager
from ai_gateway.failure import FailureClassifier, FailureType
from ai_gateway.gateway_client import GatewayClient, GatewayResponse

logger = logging.getLogger("ai_gateway.router")


class NoCandidatesAvailableError(Exception):
    """Raised when an endpoint has no candidates at all, or every
    candidate is currently in cooldown or disabled.
    """


class AllCandidatesFailedError(Exception):
    """Raised when every attempted candidate returned a failure."""

    def __init__(self, attempts: list["AttemptResult"]) -> None:
        self.attempts = attempts
        super().__init__(f"All {len(attempts)} attempted candidate(s) failed.")


@dataclass
class AttemptResult:
    candidate: Candidate
    response: GatewayResponse


@dataclass
class RouterResult:
    candidate: Candidate
    response: GatewayResponse
    attempts: list[AttemptResult]


@dataclass
class StreamRouteResult:
    """Handle to an already-open, already-confirmed-successful streaming
    candidate. `body_iter` yields the remaining raw bytes of the response;
    `close` MUST be called exactly once when the caller is done consuming
    it (normal completion, client disconnect, or any exception), to
    release the underlying HTTP connection.
    """

    candidate: Candidate
    body_iter: Any
    close: Any


class Router:
    """Attempts candidates for an endpoint in order until one succeeds, a
    global attempt cap is hit, or the candidate list is exhausted.
    """

    def __init__(
        self,
        candidates_by_endpoint: dict[str, list[Candidate]],
        cooldown_manager: CooldownManager,
        failure_classifier: FailureClassifier,
        gateway_client: GatewayClient,
        routing_config: RoutingConfig,
    ) -> None:
        self._candidates_by_endpoint = candidates_by_endpoint
        self._cooldown_manager = cooldown_manager
        self._failure_classifier = failure_classifier
        self._gateway_client = gateway_client
        self._routing_config = routing_config

    def endpoints(self) -> list[str]:
        return list(self._candidates_by_endpoint.keys())

    def has_endpoint(self, endpoint: str) -> bool:
        return endpoint in self._candidates_by_endpoint

    @property
    def candidates_by_endpoint(self) -> dict[str, list[Candidate]]:
        """Read-only view of the current candidate compilation, keyed by
        endpoint. Used by the health endpoint for reporting.
        """
        return self._candidates_by_endpoint

    def replace_candidates(self, candidates_by_endpoint: dict[str, list[Candidate]]) -> None:
        """Atomically swap in a newly compiled candidate list, used by
        /internal/reload. Cooldown state is untouched: it lives in
        CooldownManager, keyed by "provider:account", independent of this
        list.
        """
        self._candidates_by_endpoint = candidates_by_endpoint

    async def route(self, endpoint: str, payload: dict[str, Any]) -> RouterResult:
        candidates = self._candidates_by_endpoint.get(endpoint)
        if not candidates:
            raise NoCandidatesAvailableError(f"Endpoint '{endpoint}' has no configured candidates.")

        attempts: list[AttemptResult] = []
        # Separate from len(attempts): MODEL_NOT_FOUND failures that return
        # quickly don't consume the attempt budget at all (see below), so
        # this can lag behind the number of candidates actually tried.
        counted_attempts = 0
        start = time.monotonic()

        for candidate in candidates:
            if counted_attempts >= self._routing_config.max_attempts:
                logger.info(
                    "Endpoint '%s' hit max_attempts=%d before exhausting candidates.",
                    endpoint,
                    self._routing_config.max_attempts,
                )
                break

            if not self._cooldown_manager.is_available(candidate.key):
                logger.debug("Skipping candidate %s: not available (cooldown/disabled).", candidate)
                continue

            logger.info(
                "Attempt %d for endpoint '%s': provider=%s model=%s account=%s",
                len(attempts) + 1,
                endpoint,
                candidate.provider,
                candidate.model,
                candidate.account,
            )

            call_start = time.monotonic()
            response = await self._gateway_client.send_chat_completion(
                candidate,
                payload,
                timeout_seconds=self._routing_config.request_timeout_seconds,
            )
            call_elapsed = time.monotonic() - call_start
            attempts.append(AttemptResult(candidate=candidate, response=response))

            if response.success:
                self._cooldown_manager.record_success(candidate.key)
                logger.info("Endpoint '%s' succeeded via %s.", endpoint, candidate)
                return RouterResult(candidate=candidate, response=response, attempts=attempts)

            failure_type = self._failure_classifier.classify(
                status_code=response.status_code,
                body_text=response.raw_text,
                exception=response.exception,
            )

            if failure_type == FailureType.MODEL_NOT_FOUND:
                # A model-identity problem, not an account-health problem --
                # the account itself may be perfectly fine for its other
                # models, so cooldown state is deliberately left untouched.
                logger.warning(
                    "Candidate %s: model not found (status=%s). Skipping "
                    "without affecting account '%s' cooldown state.",
                    candidate,
                    response.status_code,
                    candidate.key,
                )
                if call_elapsed >= self._routing_config.model_not_found_count_threshold_seconds:
                    counted_attempts += 1
                continue

            self._cooldown_manager.record_failure(candidate.key, failure_type)
            counted_attempts += 1
            logger.warning(
                "Candidate %s failed with %s (status=%s).",
                candidate,
                failure_type.value,
                response.status_code,
            )

        elapsed = time.monotonic() - start
        logger.error(
            "Endpoint '%s' exhausted all attempts (%d attempted, %d counted) in %.2fs with no success.",
            endpoint,
            len(attempts),
            counted_attempts,
            elapsed,
        )
        if not attempts:
            raise NoCandidatesAvailableError(
                f"Endpoint '{endpoint}' has candidates, but all are currently in cooldown or disabled."
            )
        raise AllCandidatesFailedError(attempts)

    async def route_stream_start(self, endpoint: str, payload: dict[str, Any]) -> "StreamRouteResult":
        """Streaming counterpart to route(). Same candidate loop, same
        cooldown/max_attempts/MODEL_NOT_FOUND bookkeeping, but stops as
        soon as a candidate's response starts successfully rather than
        waiting for a complete body -- see OpenStreamResult's docstring
        in gateway_client.py for why streaming can't just reuse route()
        directly: once bytes are forwarded to the API caller, this
        gateway can no longer silently fall back to a different candidate
        without producing a broken/duplicated response, so fallback is
        only possible up to this point (first successful response
        headers), never after.

        Raises the same NoCandidatesAvailableError / AllCandidatesFailedError
        as route() if every candidate fails -- this always happens BEFORE
        any bytes have been sent to the API caller, so api/routes.py can
        still return a normal JSON error response exactly like the
        non-streaming path, instead of a half-sent stream.
        """
        candidates = self._candidates_by_endpoint.get(endpoint)
        if not candidates:
            raise NoCandidatesAvailableError(f"Endpoint '{endpoint}' has no configured candidates.")

        attempts: list[AttemptResult] = []
        counted_attempts = 0
        start = time.monotonic()

        for candidate in candidates:
            if counted_attempts >= self._routing_config.max_attempts:
                logger.info(
                    "Endpoint '%s' hit max_attempts=%d before exhausting candidates.",
                    endpoint,
                    self._routing_config.max_attempts,
                )
                break

            if not self._cooldown_manager.is_available(candidate.key):
                logger.debug("Skipping candidate %s: not available (cooldown/disabled).", candidate)
                continue

            logger.info(
                "Attempt %d for endpoint '%s' (stream): provider=%s model=%s account=%s",
                len(attempts) + 1,
                endpoint,
                candidate.provider,
                candidate.model,
                candidate.account,
            )

            call_start = time.monotonic()
            opened = await self._gateway_client.open_chat_completion_stream(
                candidate,
                payload,
                timeout_seconds=self._routing_config.request_timeout_seconds,
                read_timeout_seconds=self._routing_config.stream_read_timeout_seconds,
            )
            call_elapsed = time.monotonic() - call_start

            if opened.success:
                self._cooldown_manager.record_success(candidate.key)
                logger.info("Endpoint '%s' succeeded via %s (stream).", endpoint, candidate)
                return StreamRouteResult(candidate=candidate, body_iter=opened.body_iter, close=opened.close)

            # Failure bookkeeping mirrors route() exactly -- same
            # GatewayResponse-shaped fields, same classifier, same
            # MODEL_NOT_FOUND carve-out, same cooldown recording.
            response = GatewayResponse(
                success=False,
                status_code=opened.status_code,
                body=None,
                raw_text=opened.raw_text,
                exception=opened.exception,
            )
            attempts.append(AttemptResult(candidate=candidate, response=response))

            failure_type = self._failure_classifier.classify(
                status_code=response.status_code,
                body_text=response.raw_text,
                exception=response.exception,
            )

            if failure_type == FailureType.MODEL_NOT_FOUND:
                logger.warning(
                    "Candidate %s: model not found (status=%s). Skipping "
                    "without affecting account '%s' cooldown state.",
                    candidate,
                    response.status_code,
                    candidate.key,
                )
                if call_elapsed >= self._routing_config.model_not_found_count_threshold_seconds:
                    counted_attempts += 1
                continue

            self._cooldown_manager.record_failure(candidate.key, failure_type)
            counted_attempts += 1
            logger.warning(
                "Candidate %s failed with %s (status=%s) (stream).",
                candidate,
                failure_type.value,
                response.status_code,
            )

        elapsed = time.monotonic() - start
        logger.error(
            "Endpoint '%s' (stream) exhausted all attempts (%d attempted, %d counted) in %.2fs with no success.",
            endpoint,
            len(attempts),
            counted_attempts,
            elapsed,
        )
        if not attempts:
            raise NoCandidatesAvailableError(
                f"Endpoint '{endpoint}' has candidates, but all are currently in cooldown or disabled."
            )
        raise AllCandidatesFailedError(attempts)
