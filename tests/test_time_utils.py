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


def test_business_seconds_count_only_support_hours() -> None:
    from services.api.time_utils import business_seconds

    # Thursday 15 October 2026: 09:00 to 11:00 IST is one hour of support time (10:00 to 11:00).
    assert business_seconds(ist(15, 9), ist(15, 11)) == 3600
    # Closed overnight: 17:00 Thursday to 11:00 Friday is 1 hour + 1 hour.
    assert business_seconds(ist(15, 17), ist(16, 11)) == 7200
    # A whole working day is eight hours.
    assert business_seconds(ist(15, 0), ist(15, 23, 59, 59)) == 8 * 3600


def test_sunday_is_closed_and_saturday_is_open() -> None:
    from services.api.time_utils import business_seconds

    # 17 October 2026 is a Saturday, 18 October a Sunday.
    assert business_seconds(ist(17, 10), ist(17, 18)) == 8 * 3600
    assert business_seconds(ist(18, 0), ist(18, 23, 59, 59)) == 0
    # Saturday 17:00 to Monday 11:00 IST: one hour Saturday, one hour Monday.
    assert business_seconds(ist(17, 17), ist(19, 11)) == 7200


def test_business_seconds_is_zero_when_the_end_is_not_after_the_start() -> None:
    from services.api.time_utils import business_seconds

    assert business_seconds(ist(15, 12), ist(15, 12)) == 0
    assert business_seconds(ist(15, 13), ist(15, 12)) == 0
