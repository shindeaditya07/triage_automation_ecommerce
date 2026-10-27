import pytest

from services.api.grounding.plain_dates import with_plain_dates


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Delivered on 2026-10-27T05:30:00Z.", "Delivered on 27 October 2026."),
        # 20:00 UTC is already the next morning in India
        ("Shipped 2026-10-27T20:00:00Z.", "Shipped 28 October 2026."),
        ("Shipped 2026-10-27T20:00:00.123Z.", "Shipped 28 October 2026."),
        ("At 2026-10-27T10:00:00+05:30 it left.", "At 27 October 2026 it left."),
        ("At 2026-10-27 18:45:00+0000 it left.", "At 28 October 2026 it left."),
        ("Due 2026-11-03.", "Due 3 November 2026."),
        (
            "Placed 2026-10-01T00:10Z and sent 2026-10-02.",
            "Placed 1 October 2026 and sent 2 October 2026.",
        ),
        ("No dates here, order RD-10001.", "No dates here, order RD-10001."),
        ("Already plain: 27 October 2026.", "Already plain: 27 October 2026."),
        ("Not a date: 2026-13-45.", "Not a date: 2026-13-45."),
    ],
)
def test_iso_dates_become_plain_dates_in_india_time(text: str, expected: str) -> None:
    assert with_plain_dates(text) == expected
