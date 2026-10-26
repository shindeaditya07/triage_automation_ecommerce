"""Account lockout policy: pure, so the same rule runs against Postgres and in the test store.

Every login attempt is counted *before* the password is checked. The Nth consecutive attempt
locks the account immediately (assuming it will fail); a success clears the lock and the count.
Counting first means a burst of parallel guesses cannot all slip past a lock that is only set
after the first failure comes back: at most `max_failures` attempts ever reach the password check.

Each attempt after the lock expires doubles the next lock, up to a cap.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class LockoutState:
    consecutive_failures: int = 0
    locked_until: datetime | None = None


@dataclass(frozen=True)
class LockoutPolicy:
    max_failures: int
    base_seconds: int
    max_seconds: int

    def begin_attempt(self, state: LockoutState, now: datetime) -> tuple[LockoutState, int]:
        """Return the state after counting this attempt and the seconds to wait (0 = go ahead)."""
        if state.locked_until is not None and state.locked_until > now:
            return state, math.ceil((state.locked_until - now).total_seconds())
        failures = state.consecutive_failures + 1
        locked_until = None
        if failures >= self.max_failures:
            exponent = min(failures - self.max_failures, 32)
            seconds = min(self.base_seconds * 2**exponent, self.max_seconds)
            locked_until = now + timedelta(seconds=seconds)
        return LockoutState(failures, locked_until), 0
