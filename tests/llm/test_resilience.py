import pytest

from services.api.llm.errors import (
    CircuitOpenError,
    LLMInvalidOutputError,
    LLMRateLimitError,
    LLMRequestError,
    LLMServerError,
)
from services.api.llm.pricing import Price
from services.api.llm.resilience import BreakerState, CircuitBreaker, RetryPolicy, with_retries

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_errors_name_themselves_for_the_escalation_reason() -> None:
    assert LLMServerError("x").escalation_reason == "llm_error:LLMServerError"
    assert CircuitOpenError("x").escalation_reason == "llm_error:CircuitOpenError"
    assert "HTTP 401 (authentication_error)" in str(LLMRequestError(401, "authentication_error"))
    assert LLMInvalidOutputError("intent: enum").detail == "intent: enum"


def test_retry_delay_is_jittered_exponential_and_capped() -> None:
    policy = RetryPolicy(max_attempts=5, base_delay=1.0, max_delay=4.0)
    assert policy.delay(1, 1.0, None) == 1.0
    assert policy.delay(2, 1.0, None) == 2.0
    assert policy.delay(3, 1.0, None) == 4.0
    assert policy.delay(9, 1.0, None) == 4.0  # capped
    assert policy.delay(3, 0.5, None) == 2.0
    assert policy.delay(3, 0.0, None) == 0.0


def test_retry_after_wins_when_longer_but_is_bounded() -> None:
    policy = RetryPolicy()
    assert policy.delay(1, 0.0, 5.0) == 5.0
    assert policy.delay(1, 0.0, 9999.0) == 30.0


async def test_transient_errors_are_retried_then_succeed() -> None:
    calls: list[int] = []
    slept: list[float] = []

    async def call() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise LLMRateLimitError("slow down", retry_after=2.0)
        return "ok"

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    result, attempts = await with_retries(
        call, RetryPolicy(max_attempts=3), sleep=sleep, rand=lambda: 0.0
    )
    assert (result, attempts) == ("ok", 3)
    assert slept == [2.0, 2.0]


async def test_the_last_transient_error_is_raised_when_tries_run_out() -> None:
    calls: list[int] = []

    async def call() -> str:
        calls.append(1)
        raise LLMServerError("down")

    async def sleep(seconds: float) -> None:
        return None

    with pytest.raises(LLMServerError):
        await with_retries(call, RetryPolicy(max_attempts=2), sleep=sleep, rand=lambda: 0.5)
    assert len(calls) == 2


async def test_a_refused_request_is_never_retried() -> None:
    calls: list[int] = []

    async def call() -> str:
        calls.append(1)
        raise LLMRequestError(400)

    async def sleep(seconds: float) -> None:
        raise AssertionError("must not sleep")

    with pytest.raises(LLMRequestError):
        await with_retries(call, RetryPolicy(), sleep=sleep, rand=lambda: 0.0)
    assert len(calls) == 1


def test_breaker_opens_after_n_failures_and_fails_fast() -> None:
    clock = Clock()
    breaker = CircuitBreaker(3, 30.0, clock)
    for _ in range(2):
        breaker.before_call()
        breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED
    breaker.before_call()
    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    with pytest.raises(CircuitOpenError):
        breaker.before_call()


def test_a_success_resets_the_count() -> None:
    breaker = CircuitBreaker(2, 30.0, Clock())
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED


def test_breaker_lets_one_probe_through_after_the_cooldown() -> None:
    clock = Clock()
    breaker = CircuitBreaker(1, 30.0, clock)
    breaker.record_failure()
    clock.now = 29.0
    with pytest.raises(CircuitOpenError):
        breaker.before_call()
    clock.now = 30.0
    breaker.before_call()
    assert breaker.state is BreakerState.HALF_OPEN
    with pytest.raises(CircuitOpenError):
        breaker.before_call()  # the probe is still out
    breaker.record_success()
    assert breaker.state is BreakerState.CLOSED


def test_a_failed_probe_reopens_for_a_full_cooldown() -> None:
    clock = Clock()
    breaker = CircuitBreaker(5, 30.0, clock)
    for _ in range(5):
        breaker.record_failure()
    clock.now = 31.0
    breaker.before_call()
    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    clock.now = 60.0
    with pytest.raises(CircuitOpenError):
        breaker.before_call()
    clock.now = 61.0
    breaker.before_call()


def test_price_is_computed_in_decimal_from_token_counts() -> None:
    from decimal import Decimal

    price = Price(Decimal("250"), Decimal("1000"))
    assert price.cost_inr(2000, 500) == Decimal("1.0000")
    assert Price().cost_inr(10**6, 10**6) == Decimal("0.0000")
    assert Price(Decimal("1")).cost_inr(1) == Decimal("0.0000")
