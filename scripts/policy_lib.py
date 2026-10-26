"""Shared helpers for the policy integrity checks (policies.md, constants, lock file)."""

import hashlib
import re
from pathlib import Path

from services.api.policy_source import parse_passages, policies_version

__all__ = ["parse_passages", "policies_version"]  # shared with the runtime readiness check

ROOT = Path(__file__).resolve().parent.parent
POLICIES_PATH = ROOT / "docs" / "policies.md"
CONSTANTS_PATH = ROOT / "policy_constants.yaml"
LOCK_PATH = ROOT / ".policy_lock"

_TOKEN = re.compile(r"₹?\d[\d,]*")


def normalized_bytes(path: Path) -> bytes:
    """File bytes with CRLF folded to LF, so Windows checkouts hash the same as Linux."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def policies_sha256(path: Path = POLICIES_PATH) -> str:
    return hashlib.sha256(normalized_bytes(path)).hexdigest()


def number_tokens(text: str) -> set[str]:
    """Numbers in a text excerpt, commas removed; rupee amounts keep their ₹ prefix."""
    return {token.replace(",", "") for token in _TOKEN.findall(text)}


def expected_token(value: int, unit: str) -> str:
    if unit == "paise":
        if value % 100:
            raise ValueError(f"paise value {value} is not a whole number of rupees")
        return f"₹{value // 100}"
    return str(value)
