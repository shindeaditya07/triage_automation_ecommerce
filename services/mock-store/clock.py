"""The mock store's notion of 'now'. FROZEN_NOW pins it so seeded dates are reproducible."""

import os
from datetime import UTC, datetime, timedelta


def current_time() -> datetime:
    """FROZEN_NOW if set (must be an ISO-8601 UTC timestamp), otherwise the real time."""
    raw = os.environ.get("FROZEN_NOW", "").strip()
    if not raw:
        return datetime.now(UTC).replace(microsecond=0)
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(
            f"FROZEN_NOW must be a UTC timestamp such as 2026-10-15T12:00:00Z: {raw!r}"
        )
    return parsed
