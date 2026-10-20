"""The return and damage windows at their edges (RET-1.1, DMG-3.1)."""

from datetime import UTC, datetime, timedelta

from services.api.time_utils import IST, damage_window_open, return_window_open

# Delivered 11:00 IST on 10 October. The seventh calendar day after is 17 October.
DELIVERED = datetime(2026, 10, 10, 11, 0, tzinfo=IST)


def ist(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, second, tzinfo=IST)


def test_the_return_window_ends_at_the_last_second_of_the_seventh_day() -> None:
    assert return_window_open(DELIVERED, ist(17, 23, 59, 59))


def test_one_second_into_the_eighth_day_is_late() -> None:
    assert not return_window_open(DELIVERED, ist(18, 0, 0, 0))


def test_the_delivery_day_counts_by_the_ist_date_not_the_utc_date() -> None:
    late_evening = datetime(2026, 10, 10, 20, 0, tzinfo=UTC)  # 01:30 IST on the 11th
    assert return_window_open(late_evening, ist(18, 23, 59, 59))
    assert not return_window_open(late_evening, ist(19, 0, 0, 0))


def test_a_request_before_delivery_is_in_time() -> None:
    assert return_window_open(DELIVERED, ist(9, 12))


def test_the_return_window_is_judged_in_ist_whatever_zone_the_moment_is_given_in() -> None:
    assert return_window_open(DELIVERED, datetime(2026, 10, 17, 18, 29, 59, tzinfo=UTC))
    assert not return_window_open(DELIVERED, datetime(2026, 10, 17, 18, 30, 0, tzinfo=UTC))


def test_forty_eight_hours_is_in_time_and_one_second_more_is_not() -> None:
    delivered = datetime(2026, 10, 10, 5, 30, tzinfo=UTC)
    assert damage_window_open(delivered, delivered + timedelta(hours=48))
    assert not damage_window_open(delivered, delivered + timedelta(hours=48, seconds=1))


def test_a_report_before_delivery_is_in_time() -> None:
    delivered = datetime(2026, 10, 10, 5, 30, tzinfo=UTC)
    assert damage_window_open(delivered, delivered - timedelta(hours=1))
