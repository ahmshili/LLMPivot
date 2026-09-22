from __future__ import annotations

from ai_gateway.config.models import CooldownConfig
from ai_gateway.cooldown import AccountState, CooldownManager
from ai_gateway.failure import FailureType


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_manager() -> tuple[CooldownManager, FakeClock]:
    config = CooldownConfig(
        base_seconds=10,
        multiplier=2,
        max_seconds=100,
        quota_cooldown_seconds=50,
        unknown_failure_cooldown_seconds=5,
    )
    clock = FakeClock()
    return CooldownManager(config, clock=clock), clock


def test_new_account_is_available_by_default() -> None:
    manager, _ = make_manager()
    assert manager.is_available("gemini:personal") is True
    assert manager.get_state("gemini:personal") == AccountState.HEALTHY


def test_auth_failure_disables_permanently() -> None:
    manager, clock = make_manager()
    manager.record_failure("gemini:personal", FailureType.AUTH_FAILURE)
    assert manager.get_state("gemini:personal") == AccountState.DISABLED
    assert manager.is_available("gemini:personal") is False
    clock.advance(10_000)
    assert manager.is_available("gemini:personal") is False


def test_transient_failure_uses_exponential_backoff() -> None:
    manager, clock = make_manager()
    key = "gemini:personal"

    manager.record_failure(key, FailureType.TRANSIENT)
    assert manager.is_available(key) is False
    clock.advance(9.9)
    assert manager.is_available(key) is False
    clock.advance(0.2)  # total 10.1s, base_seconds=10 for first failure
    assert manager.is_available(key) is True

    # Second consecutive failure should roughly double the cooldown.
    manager.record_failure(key, FailureType.TRANSIENT)
    clock.advance(19.9)
    assert manager.is_available(key) is False
    clock.advance(0.2)  # total 20.1s, base * multiplier**1 = 20
    assert manager.is_available(key) is True


def test_backoff_capped_at_max_seconds() -> None:
    manager, clock = make_manager()
    key = "gemini:personal"
    for _ in range(10):
        manager.record_failure(key, FailureType.TRANSIENT)
        clock.advance(1000)  # always let cooldown elapse before next failure
    clock.advance(101)
    assert manager.is_available(key) is True


def test_quota_exhausted_uses_fixed_cooldown() -> None:
    manager, clock = make_manager()
    key = "gemini:personal"
    manager.record_failure(key, FailureType.QUOTA_EXHAUSTED)
    clock.advance(49)
    assert manager.is_available(key) is False
    clock.advance(2)
    assert manager.is_available(key) is True


def test_unknown_failure_uses_short_fixed_cooldown() -> None:
    manager, clock = make_manager()
    key = "gemini:personal"
    manager.record_failure(key, FailureType.UNKNOWN)
    clock.advance(4)
    assert manager.is_available(key) is False
    clock.advance(2)
    assert manager.is_available(key) is True


def test_success_resets_state() -> None:
    manager, clock = make_manager()
    key = "gemini:personal"
    manager.record_failure(key, FailureType.TRANSIENT)
    manager.record_success(key)
    assert manager.get_state(key) == AccountState.HEALTHY
    assert manager.is_available(key) is True
