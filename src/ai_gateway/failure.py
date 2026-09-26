"""FailureClassifier: turns a raw response/exception into one of a small
set of generic failure types.

This classifier deliberately knows nothing about any specific provider. It
only looks at:
    * the HTTP status code (a generic, provider-agnostic signal), and
    * a handful of generic phrases that commonly appear in quota-exhaustion
      error bodies across many providers (not tied to any one vendor's
      wording), similar to a simple spam filter.

If a clearer signal is ever needed, it should be added as another generic
pattern here -- never as provider-specific parsing.
PivotLLM -- https://github.com/ahmshili/LLMPivot -- Copyright (c) ahmshili.
Portfolio project, source-available license (see LICENSE at repo root):
view/evaluate only, no redistribution, no forks outside PRs to the
original repo, no production/commercial use without permission. This
notice must be preserved. Contact: a.shili.pers@gmail.com
"""

from __future__ import annotations

import re
from enum import Enum


class FailureType(str, Enum):
    TEMP_RATE_LIMIT = "TEMP_RATE_LIMIT"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    AUTH_FAILURE = "AUTH_FAILURE"
    NETWORK_FAILURE = "NETWORK_FAILURE"
    TRANSIENT = "TRANSIENT"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    UNKNOWN = "UNKNOWN"


# Generic phrases that tend to indicate a hard quota/billing limit rather
# than a short-lived rate limit, regardless of provider. Kept intentionally
# small and generic -- these are not provider-specific error codes.
_QUOTA_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"quota",
        r"resource[\s_-]?exhausted",
        r"billing",
        r"daily limit",
        r"monthly limit",
        r"insufficient_quota",
        r"exceeded your current quota",
    ]
]

_AUTH_STATUS_CODES = {401, 403}
_TRANSIENT_STATUS_CODES = {500, 502, 503, 504}


class FailureClassifier:
    """Stateless classifier: pure function of (status_code, body_text,
    exception) -> FailureType.
    """

    def classify(
        self,
        *,
        status_code: int | None,
        body_text: str = "",
        exception: Exception | None = None,
    ) -> FailureType:
        if exception is not None and status_code is None:
            return FailureType.NETWORK_FAILURE

        if status_code is None:
            return FailureType.UNKNOWN

        if status_code in _AUTH_STATUS_CODES:
            return FailureType.AUTH_FAILURE

        if status_code == 429:
            if self._looks_like_quota_exhaustion(body_text):
                return FailureType.QUOTA_EXHAUSTED
            return FailureType.TEMP_RATE_LIMIT

        if status_code == 404:
            # A specific, reliable signal that the model itself doesn't
            # exist (typo, deprecated ID, wrong provider path) rather than
            # anything wrong with the account. Kept distinct from the
            # generic 4xx->UNKNOWN branch below so CooldownManager can
            # avoid penalizing the account for a model-identity problem.
            return FailureType.MODEL_NOT_FOUND

        if status_code in _TRANSIENT_STATUS_CODES:
            return FailureType.TRANSIENT

        if 400 <= status_code < 500:
            # Other 4xx (bad request, unsupported operation for this model,
            # etc.) are not reliably retryable via a different account --
            # but we still classify rather than crash. Treated as UNKNOWN
            # so a single bad account doesn't get a long cooldown for what
            # might be a request-shape problem.
            return FailureType.UNKNOWN

        if status_code >= 500:
            return FailureType.TRANSIENT

        return FailureType.UNKNOWN

    @staticmethod
    def _looks_like_quota_exhaustion(body_text: str) -> bool:
        return any(pattern.search(body_text) for pattern in _QUOTA_PATTERNS)
