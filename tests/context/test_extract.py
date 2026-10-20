import pytest

from services.api.context.extract import order_ids_in


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Where is order RD-10001?", ["RD-10001"]),
        ("my order rd10001 is late", ["RD-10001"]),
        ("order #RD 10001, please", ["RD-10001"]),
        ("ORDER RD-10001.", ["RD-10001"]),
        ("(RD-10001)", ["RD-10001"]),
        ("RD‑10001", ["RD-10001"]),  # a non-breaking hyphen
        ("ＲＤ－10001", ["RD-10001"]),  # full-width letters and hyphen
        ("RD-10001 and RD-10002, also rd-10001 again", ["RD-10001", "RD-10002"]),
        ("no order here", []),
        ("order 4521 never came", []),  # a bare number is not an order id
        ("RD-12 is too short", []),
        ("BIRD-10001 is not ours", []),
        ("call me on 9876543210", []),
    ],
)
def test_order_ids_are_found_and_normalised(text: str, expected: list[str]) -> None:
    assert order_ids_in(text) == expected


def test_sources_are_read_in_order_and_none_is_skipped() -> None:
    assert order_ids_in("RD-10003", None, "", "see RD-10001 and RD-10003") == [
        "RD-10003",
        "RD-10001",
    ]
