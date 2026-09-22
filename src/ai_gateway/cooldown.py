"""CooldownManager: in-memory state tracking for each (provider, account)
pair.

No scoring, no probabilistic routing, no ML -- just three states:

    HEALTHY   -> candidate is eligible for routing.
    COOLDOWN  -> candidate is temporarily skipped until `cooldown_until`.
    DISABLED  -> candidate is permanently skipped until config reload.

State lives entirely in process memory (no Redis, no SQL), which means it
resets on restart. That's an accepted tradeoff: a restart briefly re-enables
a genuinely dead account until it fails once more.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from ai_gateway.config.models import CooldownConfig
from ai_gateway.failure import FailureType

logger = logging.getLogger("ai_gateway.cooldown")


class AccountState(str, Enum):
    HEALTHY = "healthy"
    COOLDOWN = "cooldown"
    DISABLED = "disabled"


@dataclass
class AccountRecord:
    state: AccountState = AccountState.HEALTHY
    consecutive_failures: int = 0
    cooldown_until: float | None = None
    last_failure_type: FailureType | None = None


class CooldownManager:
    """Tracks health state for every candidate key ("provider:account")."""

    def __init__(self, config: CooldownConfig, clock: Callable[[], float] = time.time) -> None:
        self._config = config
        self._clock = clock
        self._records: dict[str, AccountRecord] = {}

    def is_available(self, key: str) -> bool:
        record = self._records.get(key)
        if record is None:
            return True
        if record.state == AccountState.DISABLED:
            return False
        if record.state == AccountState.COOLDOWN:
            assert record.cooldown_until is not None
            if self._clock() >= record.cooldown_until:
                # Cooldown has elapsed; the account becomes eligible again.
                # We leave consecutive_failures intact so a further failure
                # continues the backoff curve rather than restarting it.
                record.state = AccountState.HEALTHY
                record.cooldown_until = None
                return True
            return False
        return True

    def get_state(self, key: str) -> AccountState:
        record = self._records.get(key)
        return record.state if record is not None else AccountState.HEALTHY

    def record_success(self, key: str) -> None:
        self._records[key] = AccountRecord()
        logger.info("Account '%s' recorded a success and is now HEALTHY.", key)

    def record_failure(self, key: str, failure_type: FailureType) -> None:
        record = self._records.setdefault(key, AccountRecord())
        record.last_failure_type = failure_type
        record.consecutive_failures += 1

        if failure_type == FailureType.AUTH_FAILURE:
            record.state = AccountState.DISABLED
            record.cooldown_until = None
            logger.warning("Account '%s' DISABLED due to AUTH_FAILURE.", key)
            return

        if failure_type == FailureType.QUOTA_EXHAUSTED:
            duration = self._config.quota_cooldown_seconds
        elif failure_type == FailureType.UNKNOWN:
            duration = self._config.unknown_failure_cooldown_seconds
        else:
            # TEMP_RATE_LIMIT, TRANSIENT, NETWORK_FAILURE -> exponential backoff.
            duration = min(
                self._config.base_seconds * (self._config.multiplier ** (record.consecutive_failures - 1)),
                self._config.max_seconds,
            )

        record.state = AccountState.COOLDOWN
        record.cooldown_until = self._clock() + duration
        logger.info(
            "Account '%s' entered COOLDOWN for %.1fs after %s (consecutive_failures=%d).",
            key,
            duration,
            failure_type.value,
            record.consecutive_failures,
        )

    def snapshot(self) -> dict[str, dict[str, object]]:
        """Return a plain-dict snapshot of all tracked accounts, suitable
        for the health endpoint.
        """
        result: dict[str, dict[str, object]] = {}
        for key, record in self._records.items():
            result[key] = {
                "state": record.state.value,
                "consecutive_failures": record.consecutive_failures,
                "cooldown_until": record.cooldown_until,
                "last_failure_type": (
                    record.last_failure_type.value if record.last_failure_type else None
                ),
            }
        return result
