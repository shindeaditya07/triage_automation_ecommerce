"""The policy time windows. Every function takes the moment to judge as an argument: nothing here
reads the clock, so a test (or a retried job) gets the same answer for the same input.

The moment to judge is when the customer wrote, not when the job happens to run.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from services.api.policy_constants import constant

IST = ZoneInfo("Asia/Kolkata")


def ist_date(moment: datetime) -> date:
    return moment.astimezone(IST).date()


def return_window_open(delivered_at: datetime, at: datetime) -> bool:
    """RET-1.1: open until 23:59:59 IST on the seventh calendar day after the delivery date
    (inclusive), the delivery date being the courier timestamp converted to IST. A request at
    00:00:00 IST on the eighth day is late."""
    last_day = ist_date(delivered_at) + timedelta(days=constant("return_window_days"))
    return ist_date(at) <= last_day


def damage_window_open(delivered_at: datetime, at: datetime) -> bool:
    """DMG-3.1: 48 hours from the delivery timestamp, exactly. Forty-eight hours is in time, one
    second more is not."""
    return at - delivered_at <= timedelta(hours=constant("damage_window_hours"))


SUPPORT_OPEN_HOUR = 10  # FAQ-10.6: Monday to Saturday, 10:00 to 18:00 IST
SUPPORT_CLOSE_HOUR = 18
SUNDAY = 6


def business_seconds(start: datetime, end: datetime) -> float:
    """Seconds between `start` and `end` that fall inside support hours (Monday to Saturday,
    10:00 to 18:00 IST). Public holidays are not excluded. Zero when `end` is not after `start`."""
    if end <= start:
        return 0.0
    total = 0.0
    day = ist_date(start)
    last = ist_date(end)
    while day <= last:
        if day.weekday() != SUNDAY:
            opens = datetime(day.year, day.month, day.day, SUPPORT_OPEN_HOUR, tzinfo=IST)
            closes = datetime(day.year, day.month, day.day, SUPPORT_CLOSE_HOUR, tzinfo=IST)
            overlap = (min(end, closes) - max(start, opens)).total_seconds()
            total += max(overlap, 0.0)
        day += timedelta(days=1)
    return total
