"""Timestamps in a customer reply are written as plain dates ("27 October 2026").

The order record holds ISO timestamps in UTC ("2026-10-27T05:30:00Z"), and the model sometimes
copies one into the reply as it is. A customer should not read that, and the UTC date can be a day
off the date in India, which is how the store (and the grounding check, rule G-4's dates) counts a
day. So the code rewrites any ISO date or timestamp in the reply to the day, month name and year in
Indian time and drops the time of day. The reply is rewritten before grounding, so the check sees
the date the customer will read.
"""

import re
from datetime import date, datetime

from services.api.time_utils import IST

_ISO = re.compile(
    r"\b(\d{4})-(\d{2})-(\d{2})"
    r"(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s?(Z|[+-]\d{2}:?\d{2})?)?\b"
)
_TZ_SUFFIX = re.compile(r"(?<=\d)([+-]\d{2})(\d{2})$")


def _plain(match: re.Match[str]) -> str:
    try:
        year, month, day = (int(match.group(n)) for n in (1, 2, 3))
        if match.group(4) is None:
            moment = date(year, month, day)
        else:
            zone = match.group(7) or "Z"
            text = f"{year:04d}-{month:02d}-{day:02d}T{match.group(4)}:{match.group(5)}"
            text += f":{match.group(6) or '00'}"
            text += "+00:00" if zone == "Z" else _TZ_SUFFIX.sub(r"\1:\2", zone)
            moment = datetime.fromisoformat(text).astimezone(IST).date()
    except ValueError:
        return match.group()  # not a real date: leave it for the grounding check to judge
    return f"{moment.day} {moment:%B %Y}"


def with_plain_dates(reply_text: str) -> str:
    """The reply with every ISO date or timestamp written as "27 October 2026" (India's date)."""
    return _ISO.sub(_plain, reply_text)
