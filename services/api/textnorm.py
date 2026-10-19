"""Text normalisation for the deterministic rules.

Customers (and attackers) disguise words with invisible characters, full-width letters, accents,
look-alike letters from other alphabets, stretched letters and digit substitutions. Rules match
against two views of the message so a disguised word is still found:

- the plain view: Unicode-folded, lower-case, whitespace collapsed
- the de-obfuscated view: the plain view with digits and symbols read as the letters they imitate

Devanagari and other non-Latin scripts pass through untouched.
"""

import re
import unicodedata

# Look-alike Cyrillic and Greek letters, folded to the Latin letter they imitate.
_CONFUSABLES = str.maketrans(
    {
        "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p",
        "с": "c", "т": "t", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s",
        "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t",
        "υ": "u", "χ": "x",
    }
)  # fmt: skip
_STRETCHED = re.compile(r"([a-z])\1{2,}")
_WHITESPACE = re.compile(r"\s+")
# A digit or symbol is read as a letter only when it touches a letter, so "order 10" and
# "RD-10001" are left alone while "5hit" and "idi0t" are read as words.
_LEET_CHARS = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"}
_LEET = re.compile(r"(?<=[a-z])[013457@$]|[013457@$](?=[a-z])")


def _strip_accent(char: str) -> str:
    """'é' becomes 'e'; anything whose base is not an ASCII letter is kept as it is."""
    if char.isascii():
        return char
    decomposed = unicodedata.normalize("NFKD", char)
    base, rest = decomposed[0], decomposed[1:]
    if base.isascii() and base.isalpha() and all(unicodedata.category(c) == "Mn" for c in rest):
        return base
    return char


def plain(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text)
    folded = "".join(c for c in folded if unicodedata.category(c) != "Cf")
    folded = "".join(_strip_accent(c) for c in folded)
    folded = folded.translate(_CONFUSABLES).casefold()
    folded = _STRETCHED.sub(r"\1", folded)
    return _WHITESPACE.sub(" ", folded).strip()


def deobfuscated(plain_text: str) -> str:
    # Repeat until stable so "a$$" reads as "ass": the second "$" only touches a letter once the
    # first has been replaced.
    text = plain_text
    while True:
        replaced = _LEET.sub(lambda m: _LEET_CHARS[m.group()], text)
        if replaced == text:
            return text
        text = replaced


def views(text: str) -> tuple[str, str]:
    """The plain view and the de-obfuscated view of `text`."""
    base = plain(text)
    return base, deobfuscated(base)
