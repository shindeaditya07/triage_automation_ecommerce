import pytest

from services.api import textnorm


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Re​fund", "refund"),  # zero-width space
        ("ｌａｗｙｅｒ", "lawyer"),  # full-width
        ("lawyér", "lawyer"),  # accent
        ("lаwyer", "lawyer"),  # Cyrillic a
        ("  LAWYER\n\t now ", "lawyer now"),
        ("fuuuuck", "fuck"),  # stretched
        ("book", "book"),  # a double letter is real
        ("वकील", "वकील"),  # Devanagari passes through untouched
        ("क़ीमत", "क़ीमत"),
    ],
)
def test_plain_view(raw: str, expected: str) -> None:
    assert textnorm.plain(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("sh1t", "shit"),
        ("$hit", "shit"),
        ("idi0t", "idiot"),
        ("a$$", "ass"),
        ("order 10001", "order 10001"),  # digits alone are not letters
        ("RD-10001", "rd-10001"),
        ("100%", "100%"),
    ],
)
def test_deobfuscated_view(raw: str, expected: str) -> None:
    assert textnorm.views(raw)[1] == expected
