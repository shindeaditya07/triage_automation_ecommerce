"""The clock business logic reads. Inject one in tests so time can be frozen or advanced."""

from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)
