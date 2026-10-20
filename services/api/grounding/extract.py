"""Pulling checkable values out of text: amounts, dates, durations, identifiers, sentences.

The same extractors read the reply and the policy passages, so "7 days" in a reply and "7 calendar
days" in a passage come out the same.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

_MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
_NUMBER_WORDS = {
    word: number
    for number, word in enumerate(
        ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"),
    )
}
_NUMBER = r"\d+|" + "|".join(_NUMBER_WORDS)

_AMOUNT = re.compile(r"(?:₹|\brs\.?|\binr)\s?(\d[\d,]*(?:\.\d{1,2})?)", re.IGNORECASE)
_DURATION = re.compile(
    rf"\b({_NUMBER})(?:(?:\s?[\u2013\u2014-]\s?|\s+to\s+)({_NUMBER}))?\s*(?:calendar\s+|business\s+|working\s+)?"
    r"(hours?|hrs?|days?|weeks?|months?)\b",
    re.IGNORECASE,
)
_DATE_DMY = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH})\b\.?,?(?:\s+(\d{{4}}))?", re.IGNORECASE
)
_DATE_MDY = re.compile(
    rf"\b({_MONTH})\b\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", re.I
)
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DATE_SLASH = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
_POLICY_ID = re.compile(r"\b[A-Z]{3}-\d+\.\d+\b")

_UNIT = {"h": "hour", "hr": "hour", "d": "day", "w": "week", "m": "month"}


@dataclass(frozen=True)
class Found[T]:
    """A value found in text, with the text it came from."""

    value: T
    span: str


@dataclass(frozen=True)
class Duration:
    low: int
    high: int
    unit: str  # hour, day, week or month


@dataclass(frozen=True)
class DayOfYear:
    """A date as written: the year is None when the text gives none."""

    day: int
    month: int
    year: int | None

    def matches(self, other: date) -> bool:
        return (self.day, self.month) == (other.day, other.month) and (
            self.year is None or self.year == other.year
        )


def normalise(text: str) -> str:
    """Curly quotes straightened and whitespace collapsed, for phrase matching."""
    straight = text.translate({0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"'})
    return re.sub(r"\s+", " ", straight)


def _number(token: str) -> int:
    return int(token) if token.isdigit() else _NUMBER_WORDS[token.lower()]


def amounts(text: str) -> list[Found[int]]:
    """Rupee amounts, as paise."""
    found: list[Found[int]] = []
    for match in _AMOUNT.finditer(text):
        try:
            rupees = Decimal(match.group(1).replace(",", ""))
        except InvalidOperation:  # pragma: no cover - the pattern only admits digits
            continue
        found.append(Found(int(rupees * 100), match.group(0)))
    return found


def durations(text: str) -> list[Found[Duration]]:
    found: list[Found[Duration]] = []
    for match in _DURATION.finditer(text):
        low = _number(match.group(1))
        high = _number(match.group(2)) if match.group(2) else low
        unit = match.group(3).lower().rstrip("s")
        unit = unit if unit in ("hour", "day", "week", "month") else _UNIT[unit[0]]
        found.append(Found(Duration(min(low, high), max(low, high), unit), match.group(0)))
    return found


def dates(text: str) -> list[Found[DayOfYear]]:
    found: list[Found[DayOfYear]] = []
    taken: list[tuple[int, int]] = []

    def add(match: re.Match[str], day: int, month: int, year: int | None) -> None:
        if any(start < match.end() and match.start() < end for start, end in taken):
            return
        if 1 <= day <= 31 and 1 <= month <= 12:
            taken.append((match.start(), match.end()))
            found.append(Found(DayOfYear(day, month, year), match.group(0).strip(" ,.")))

    for match in _DATE_ISO.finditer(text):
        add(match, int(match.group(3)), int(match.group(2)), int(match.group(1)))
    for match in _DATE_SLASH.finditer(text):
        add(match, int(match.group(1)), int(match.group(2)), int(match.group(3)))
    for match in _DATE_DMY.finditer(text):
        year = int(match.group(3)) if match.group(3) else None
        add(match, int(match.group(1)), _MONTHS[match.group(2).lower()], year)
    for match in _DATE_MDY.finditer(text):
        year = int(match.group(3)) if match.group(3) else None
        add(match, int(match.group(2)), _MONTHS[match.group(1).lower()], year)
    return found


def matches_of(patterns: list[re.Pattern[str]], text: str) -> list[str]:
    """Every distinct upper-cased match of any pattern, in order of appearance."""
    seen: list[str] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            value = match.group(0).upper().replace(" ", "")
            if value not in seen:
                seen.append(value)
    return seen


def emails(text: str) -> list[str]:
    return [match.group(0).lower() for match in _EMAIL.finditer(text)]


def policy_ids(text: str) -> list[str]:
    return _POLICY_ID.findall(text)


def sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_END.split(text) if part.strip()]
