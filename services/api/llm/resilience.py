"""Bounded retries and a circuit breaker, so a provider outage becomes an escalation, not a hang.

Both take their clock, sleep and randomness as arguments, so tests run in no time and are exact.
"""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from services.api.llm.errors import CircuitOpenError, LLMTransientError

MAX_RETRY_AFTER_SECONDS = 30.0


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3  # tries in all, the first included
    base_delay: float = 0.5
    max_delay: float = 8.0

    def delay(self, attempt: int, rand: float, retry_after: float | None) -> float:
        """Seconds to wait after failed attempt number `attempt`: exponential backoff with full
        jitter (a random point between zero and the cap), or the provider's own Retry-After if
        that is longer, up to a ceiling."""
        cap = min(self.max_delay, self.base_delay * (1 << (attempt - 1)))
        wait = rand * cap
        if retry_after is not None:
            wait = max(wait, min(retry_after, MAX_RETRY_AFTER_SECONDS))
        return wait


async def with_retries[T](
    call: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    sleep: Callable[[float], Awaitable[None]],
    rand: Callable[[], float],
) -> tuple[T, int]:
    """Run `call`, trying again after transient errors only. Returns the result and the number of
    tries it took. Any other error, and the last transient one, is raised as it is."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return await call(), attempt
        except LLMTransientError as exc:
            if attempt >= policy.max_attempts:
                raise
            await sleep(policy.delay(attempt, rand(), exc.retry_after))


class BreakerState(StrEnum):
    CLOSED = "closed"  # calls go through
    OPEN = "open"  # calls are refused until the cooldown ends
    HALF_OPEN = "half_open"  # one probe call is allowed to see whether the provider is back


class CircuitBreaker:
    """Opens after `failure_threshold` failed calls in a row; after `cooldown_seconds` lets one
    probe through. A probe that works closes it; one that fails opens it for another cooldown.

    A "failed call" is one that ran out of retries on a transient error. A provider that answered
    (even with a refusal or unusable output) is up, so that counts as a success.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._monotonic = monotonic
        self._failures = 0
        self._opened_at = 0.0
        self._state = BreakerState.CLOSED

    @property
    def state(self) -> BreakerState:
        return self._state

    def before_call(self) -> None:
        """Raise `CircuitOpenError` if the call must not be made."""
        if self._state is BreakerState.CLOSED:
            return
        if (
            self._state is BreakerState.OPEN
            and self._monotonic() - self._opened_at >= self._cooldown
        ):
            self._state = BreakerState.HALF_OPEN
            return  # this caller is the probe
        raise CircuitOpenError("the model provider is failing; calls are paused")

    def record_success(self) -> None:
        self._failures = 0
        self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        self._failures += 1
        if self._state is BreakerState.HALF_OPEN or self._failures >= self._threshold:
            self._state = BreakerState.OPEN
            self._opened_at = self._monotonic()
