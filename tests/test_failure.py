from __future__ import annotations

import httpx

from ai_gateway.failure import FailureClassifier, FailureType


def test_auth_failure_classification() -> None:
    classifier = FailureClassifier()
    assert classifier.classify(status_code=401, body_text="unauthorized") == FailureType.AUTH_FAILURE
    assert classifier.classify(status_code=403, body_text="forbidden") == FailureType.AUTH_FAILURE


def test_quota_exhausted_classification() -> None:
    classifier = FailureClassifier()
    assert (
        classifier.classify(status_code=429, body_text="You have exceeded your current quota")
        == FailureType.QUOTA_EXHAUSTED
    )
    assert (
        classifier.classify(status_code=429, body_text="RESOURCE_EXHAUSTED: billing required")
        == FailureType.QUOTA_EXHAUSTED
    )


def test_temp_rate_limit_classification() -> None:
    classifier = FailureClassifier()
    assert (
        classifier.classify(status_code=429, body_text="Too many requests, please slow down")
        == FailureType.TEMP_RATE_LIMIT
    )


def test_transient_classification() -> None:
    classifier = FailureClassifier()
    for status in (500, 502, 503, 504):
        assert classifier.classify(status_code=status, body_text="") == FailureType.TRANSIENT


def test_network_failure_classification() -> None:
    classifier = FailureClassifier()
    exc = httpx.ConnectError("connection refused")
    assert classifier.classify(status_code=None, body_text="", exception=exc) == FailureType.NETWORK_FAILURE


def test_unknown_classification_for_other_4xx() -> None:
    classifier = FailureClassifier()
    assert classifier.classify(status_code=400, body_text="bad request") == FailureType.UNKNOWN
    assert classifier.classify(status_code=422, body_text="unprocessable") == FailureType.UNKNOWN


def test_model_not_found_classification() -> None:
    classifier = FailureClassifier()
    assert classifier.classify(status_code=404, body_text="model not found") == FailureType.MODEL_NOT_FOUND


def test_unknown_when_no_status_and_no_exception() -> None:
    classifier = FailureClassifier()
    assert classifier.classify(status_code=None, body_text="") == FailureType.UNKNOWN
