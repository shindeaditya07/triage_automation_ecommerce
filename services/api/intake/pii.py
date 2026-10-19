"""Mask card, bank, UPI and government-ID numbers before a message is stored or sent to a model.

What is masked, and what deliberately is not:

* Card numbers: digit runs of 13 to 19 digits that pass the Luhn check and have a plausible
  length for their first digits, plus any 4-4-4-4 or 4-6-5 grouping that looks like a card even if
  the check digit is wrong. A card number is never stored in any form.
* Aadhaar: 12 digits grouped 4-4-4, or 12 unbroken digits whose Verhoeff check digit is valid.
* PAN: five letters, four digits, a letter, with a valid holder-type letter.
* UPI IDs: `name@handle` where the handle has no dot (an email address has one).
* Bank account numbers: 9 to 18 digits right after "account", "acct" or "a/c". A bare long number
  could be an order, tracking or phone number, and masking those would cost more than it saves.

Order numbers, tracking numbers, phone numbers and email addresses are left alone.
"""

import re
from dataclasses import dataclass, field

CARD = "card"
AADHAAR = "aadhaar"
PAN = "pan"
UPI = "upi"
BANK_ACCOUNT = "bank_account"

PLACEHOLDERS = {
    CARD: "[card number removed]",
    AADHAAR: "[ID number removed]",
    PAN: "[PAN removed]",
    UPI: "[UPI ID removed]",
    BANK_ACCOUNT: "[account number removed]",
}

_CARD_GROUPED = re.compile(
    r"(?<!\d)(?:\d{4}([ -])\d{4}\1\d{4}\1\d{4}|\d{4}([ -])\d{6}\2\d{5})(?!\d)"
)
_DIGIT_RUN = re.compile(r"(?<![\d])\d(?:[ -]?\d){11,21}(?![\d])")
_AADHAAR_GROUPED = re.compile(r"(?<!\d)[2-9]\d{3}([ -])\d{4}\1\d{4}(?!\d)")
_AADHAAR_PLAIN = re.compile(r"(?<!\d)[2-9]\d{11}(?!\d)")
_PAN = re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z](?![A-Za-z0-9])")
_UPI = re.compile(
    r"(?i)(?<![\w.@-])[a-z0-9][a-z0-9._-]{1,63}@[a-z][a-z0-9]{1,31}(?![\w@-])(?!\.\w)"
)
_BANK = re.compile(r"(?i)\b(?:a/c|acct?|account)\b([^\d\n]{0,25}?)(\d[\d -]{7,22}\d)")

_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


@dataclass
class Masked:
    text: str
    counts: dict[str, int] = field(default_factory=dict)


def luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def verhoeff_valid(digits: str) -> bool:
    check = 0
    for index, char in enumerate(reversed(digits)):
        check = _VERHOEFF_D[check][_VERHOEFF_P[index % 8][int(char)]]
    return check == 0


def _plausible_card(digits: str) -> bool:
    """A Luhn-valid number whose length fits the card network its first digits point to."""
    if not luhn_valid(digits):
        return False
    if len(digits) == 16:
        return digits[0] in "234568"
    if len(digits) == 15:
        return digits[:2] in {"34", "37"}
    if len(digits) == 14:
        return digits[:2] in {"30", "36", "38", "39"}
    return len(digits) in {13, 19} and digits[0] == "4"


def mask_pii(text: str) -> Masked:
    counts: dict[str, int] = {}

    def bump(kind: str) -> str:
        counts[kind] = counts.get(kind, 0) + 1
        return PLACEHOLDERS[kind]

    text = _CARD_GROUPED.sub(lambda _: bump(CARD), text)

    def card_run(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group())
        return bump(CARD) if _plausible_card(digits) else match.group()

    text = _DIGIT_RUN.sub(card_run, text)
    text = _AADHAAR_GROUPED.sub(lambda _: bump(AADHAAR), text)
    text = _AADHAAR_PLAIN.sub(
        lambda m: bump(AADHAAR) if verhoeff_valid(m.group()) else m.group(), text
    )
    text = _PAN.sub(lambda _: bump(PAN), text)
    text = _UPI.sub(lambda _: bump(UPI), text)

    def bank(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(2))
        if not 9 <= len(digits) <= 18:
            return match.group()
        return match.group()[: match.start(2) - match.start()] + bump(BANK_ACCOUNT)

    text = _BANK.sub(bank, text)
    return Masked(text, counts)
