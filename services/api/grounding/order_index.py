"""An order snapshot as the validator reads it: which fields exist, and which hold which values.

Paths are dotted with list positions dropped, so `items.0.unit_price_paise` is
`items.unit_price_paise`. A citation of `items` covers every field under it.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from services.api.time_utils import IST

_ISO_MOMENT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
_PREFIXES = ("orders.", "order.", "order_record.")


def normalise_ref(ref: str) -> str:
    """A citation's field name as a path: a leading `orders.` is dropped, `[0]` and `.0` too."""
    path = ref.strip()
    for prefix in _PREFIXES:
        if path.startswith(prefix):
            path = path[len(prefix) :]
            break
    path = re.sub(r"\[\d+\]", "", path)
    return ".".join(part for part in path.split(".") if not part.isdigit())


@dataclass
class OrderIndex:
    paths: set[str] = field(default_factory=set)
    amount_values: list[tuple[str, int]] = field(default_factory=list)
    dates: list[tuple[str, date]] = field(default_factory=list)
    tokens: list[tuple[str, str]] = field(default_factory=list)  # path, upper-cased string
    text: str = ""  # every string value, lower-cased, for "does the order say this"

    @classmethod
    def build(cls, order: dict[str, Any]) -> "OrderIndex":
        index = cls()
        strings: list[str] = []
        index._walk(order, "", strings)
        index.text = " ".join(strings).lower()
        return index

    def _walk(self, node: Any, path: str, strings: list[str]) -> None:
        if path:
            self.paths.add(path)
        if isinstance(node, dict):
            for key, value in node.items():
                self._walk(value, f"{path}.{key}" if path else key, strings)
        elif isinstance(node, list):
            for value in node:
                self._walk(value, path, strings)
        elif isinstance(node, bool) or node is None:
            return
        elif isinstance(node, int):
            if path.endswith("_paise"):
                self.amount_values.append((path, node))
        elif isinstance(node, str):
            strings.append(node)
            if _ISO_MOMENT.match(node):
                moment = datetime.fromisoformat(node.replace("Z", "+00:00"))
                self.dates.append((path, moment.astimezone(IST).date()))
            else:
                self.tokens.append((path, node.upper()))

    def has(self, ref: str) -> bool:
        return normalise_ref(ref) in self.paths

    def amount_paths(self, paise: int) -> set[str]:
        return {path for path, value in self.amount_values if value == paise}

    def date_paths(self, day: Any) -> set[str]:
        return {path for path, value in self.dates if day.matches(value)}

    def token_paths(self, token: str) -> set[str]:
        return {path for path, value in self.tokens if value == token}


def covered(cited: set[str], paths: set[str]) -> bool:
    """Whether a citation of any path in `cited` covers a field in `paths`."""
    return any(path == ref or path.startswith(ref + ".") for path in paths for ref in cited)
