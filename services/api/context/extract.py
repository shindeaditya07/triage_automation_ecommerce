"""Finding order IDs in a customer's words.

Store order IDs look like `RD-10001`. Customers write them as `rd10001`, `RD 10001`, `#RD-10001`
or with look-alike characters, so the text goes through the same normalisation as the keyword
backstop first. Only that prefixed shape is recognised: a bare number such as "order 4521" could
be a phone number, an amount or someone else's order, and a wrong guess would put an unrelated
order in front of the drafter.
"""

import re

from services.api.textnorm import plain

# Hyphen-like characters: ASCII, U+2010..2015 (hyphens and dashes), U+2212 (minus).
_ORDER_ID = re.compile(r"\brd[\s\-\u2010-\u2015\u2212]?(\d{3,12})\b")


def order_ids_in(*texts: str | None) -> list[str]:
    """Distinct order IDs found in `texts`, in order of first appearance, as `RD-<digits>`."""
    found: list[str] = []
    for text in texts:
        if not text:
            continue
        for match in _ORDER_ID.finditer(plain(text)):
            order_id = f"RD-{match.group(1)}"
            if order_id not in found:
                found.append(order_id)
    return found
