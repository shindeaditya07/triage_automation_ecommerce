from datetime import timedelta

import pytest

from services.api.auth.lockout import LockoutPolicy, LockoutState
from services.api.auth.ratelimit import SlidingWindowLimiter, client_ip
from tests.auth.conftest import START, FakeClock

POLICY = LockoutPolicy(max_failures=3, base_seconds=60, max_seconds=500)


def test_attempts_below_the_limit_are_allowed_and_counted() -> None:
    state = LockoutState()
    for expected in (1, 2):
        state, wait = POLICY.begin_attempt(state, START)
        assert (state.consecutive_failures, state.locked_until, wait) == (expected, None, 0)


def test_the_nth_attempt_locks_immediately_for_the_base_duration() -> None:
    state = LockoutState(2, None)
    state, wait = POLICY.begin_attempt(state, START)
    assert wait == 0  # this attempt still gets checked
    assert state.locked_until == START + timedelta(seconds=60)


def test_while_locked_attempts_are_refused_and_not_counted() -> None:
    locked = LockoutState(3, START + timedelta(seconds=60))
    state, wait = POLICY.begin_attempt(locked, START + timedelta(seconds=10))
    assert state == locked
    assert wait == 50


def test_the_wait_rounds_up() -> None:
    locked = LockoutState(3, START + timedelta(seconds=60))
    _, wait = POLICY.begin_attempt(locked, START + timedelta(seconds=59, milliseconds=100))
    assert wait == 1


def test_each_attempt_after_a_lock_expires_doubles_the_next_lock_up_to_the_cap() -> None:
    state = LockoutState(3, START + timedelta(seconds=60))
    now = START
    waits = []
    for _ in range(5):
        now = state.locked_until + timedelta(seconds=1)  # type: ignore[operator]
        state, wait = POLICY.begin_attempt(state, now)
        assert wait == 0
        waits.append(int((state.locked_until - now).total_seconds()))  # type: ignore[operator]
    assert waits == [120, 240, 480, 500, 500]


def test_an_enormous_failure_count_does_not_overflow() -> None:
    state, _ = POLICY.begin_attempt(LockoutState(10**9, None), START)
    assert state.locked_until == START + timedelta(seconds=500)


@pytest.mark.parametrize("max_failures", [1, 2])
def test_a_limit_of_one_locks_on_the_first_attempt(max_failures: int) -> None:
    state, _ = LockoutPolicy(max_failures, 10, 100).begin_attempt(
        LockoutState(max_failures - 1, None), START
    )
    assert state.locked_until == START + timedelta(seconds=10)


def test_limiter_allows_up_to_the_limit_then_reports_the_wait(clock: FakeClock) -> None:
    limiter = SlidingWindowLimiter(3, 60, clock)
    assert [limiter.hit("a") for _ in range(3)] == [0, 0, 0]
    assert limiter.hit("a") == 60
    clock.advance(seconds=30)
    assert limiter.hit("a") == 30


def test_limiter_frees_up_as_the_window_slides(clock: FakeClock) -> None:
    limiter = SlidingWindowLimiter(2, 60, clock)
    assert limiter.hit("a") == 0
    clock.advance(seconds=40)
    assert limiter.hit("a") == 0
    assert limiter.hit("a") > 0
    clock.advance(seconds=21)  # the first hit is now outside the window
    assert limiter.hit("a") == 0


def test_limiter_keys_are_independent(clock: FakeClock) -> None:
    limiter = SlidingWindowLimiter(1, 60, clock)
    assert limiter.hit("a") == 0
    assert limiter.hit("b") == 0
    assert limiter.hit("a") > 0


def test_limiter_memory_is_bounded(clock: FakeClock) -> None:
    limiter = SlidingWindowLimiter(5, 60, clock, max_keys=10)
    for i in range(100):
        limiter.hit(f"ip-{i}")
    assert len(limiter._events) <= 10
    clock.advance(seconds=120)
    limiter.hit("late")
    assert len(limiter._events) <= 10


def test_client_ip_uses_the_socket_peer_when_no_proxy_is_trusted() -> None:
    assert client_ip({"x-forwarded-for": "6.6.6.6"}, "10.0.0.1", 0) == "10.0.0.1"
    assert client_ip({}, None, 0) == "unknown"


def test_client_ip_takes_the_entry_the_trusted_proxy_added() -> None:
    headers = {"x-forwarded-for": "6.6.6.6, 203.0.113.9"}  # the left entry is caller-supplied
    assert client_ip(headers, "10.0.0.1", 1) == "203.0.113.9"
    assert client_ip({"x-forwarded-for": "1.1.1.1, 2.2.2.2, 3.3.3.3"}, "10.0.0.1", 2) == "2.2.2.2"


@pytest.mark.parametrize("headers", [{}, {"x-forwarded-for": ""}, {"x-forwarded-for": "not-an-ip"}])
def test_client_ip_falls_back_to_the_peer_when_the_header_is_unusable(
    headers: dict[str, str],
) -> None:
    assert client_ip(headers, "10.0.0.1", 1) == "10.0.0.1"


def test_client_ip_with_too_few_entries_falls_back_to_the_peer() -> None:
    assert client_ip({"x-forwarded-for": "1.1.1.1"}, "10.0.0.1", 2) == "10.0.0.1"
