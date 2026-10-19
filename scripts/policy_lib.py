"""Shared helpers for the policy integrity checks (policies.md, constants, lock file)."""

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICIES_PATH = ROOT / "docs" / "policies.md"
CONSTANTS_PATH = ROOT / "policy_constants.yaml"
LOCK_PATH = ROOT / ".policy_lock"

_PASSAGE_START = re.compile(r"^\*\*([A-Z]{3}-\d+\.\d+)\b")
_VERSION = re.compile(r"Policy version \*\*([\w.]+)\*\*")
_TOKEN = re.compile(r"₹?\d[\d,]*")


def normalized_bytes(path: Path) -> bytes:
    """File bytes with CRLF folded to LF, so Windows checkouts hash the same as Linux."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def policies_sha256(path: Path = POLICIES_PATH) -> str:
    return hashlib.sha256(normalized_bytes(path)).hexdigest()


def policies_version(text: str) -> str:
    match = _VERSION.search(text)
    if match is None:
        raise ValueError("policies.md has no 'Policy version **X**' header")
    return match.group(1)


def parse_passages(text: str) -> dict[str, str]:
    """Map policy ID -> passage text (bold removed; ends at the next ID, rule or heading)."""
    passages: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.replace("\r\n", "\n").split("\n"):
        start = _PASSAGE_START.match(raw)
        if start:
            current = start.group(1)
            passages[current] = []
        elif raw.strip() == "---" or raw.startswith("#"):
            current = None
        if current is not None:
            passages[current].append(raw.replace("**", ""))
    return {pid: "\n".join(lines) for pid, lines in passages.items()}


def number_tokens(text: str) -> set[str]:
    """Numbers in a text excerpt, commas removed; rupee amounts keep their ₹ prefix."""
    return {token.replace(",", "") for token in _TOKEN.findall(text)}


def expected_token(value: int, unit: str) -> str:
    if unit == "paise":
        if value % 100:
            raise ValueError(f"paise value {value} is not a whole number of rupees")
        return f"₹{value // 100}"
    return str(value)
