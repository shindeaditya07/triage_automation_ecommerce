"""Script-based check for messages the system cannot read (spec case 29).

The classifier is not asked what language a message is in, and a confident model can classify
Hindi text as an ordinary order question. This counts letters instead: a message with a large
share of non-Latin letters (Devanagari, Tamil, Arabic, Cyrillic, CJK and so on) goes to the owner.

What it does not catch: another language written in Latin letters ("mera order nahi aaya",
French, Spanish). Telling those apart needs a language model, which this deliberately is not. A
few stray non-Latin characters in an English message stay below the share and do not escalate.
"""

import unicodedata
from functools import lru_cache

NON_LATIN_SHARE = 0.30  # of all letters; at or above this the message escalates
MIN_NON_LATIN_LETTERS = 3  # a single borrowed word or name is not a language


@lru_cache(maxsize=4096)
def _is_non_latin_letter(char: str) -> bool:
    if not char.isalpha():
        return False
    return not unicodedata.name(char, "").startswith("LATIN")


def is_non_english(subject: str | None, body: str) -> bool:
    text = "".join(c for c in f"{subject or ''}{body}" if c.isalpha())
    count = sum(_is_non_latin_letter(c) for c in text)
    return count >= MIN_NON_LATIN_LETTERS and count / len(text) >= NON_LATIN_SHARE
