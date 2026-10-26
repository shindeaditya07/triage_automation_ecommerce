"""Runtime access to docs/policies.md: its version header and the policy IDs it defines.

The integrity checks (`scripts/policy_lib.py`) and the readiness check share these parsers, so
"which passages exist" has one definition. The file path comes from POLICIES_PATH or the repo's
docs/policies.md (the API image copies it to the same relative place).
"""

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "docs" / "policies.md"

_PASSAGE_START = re.compile(r"^\*\*([A-Z]{3}-\d+\.\d+)\b")
_VERSION = re.compile(r"Policy version \*\*([\w.]+)\*\*")


def policies_path() -> Path:
    return Path(os.environ.get("POLICIES_PATH") or DEFAULT_PATH)


@lru_cache(maxsize=1)
def loaded_passages() -> dict[str, str]:
    """Policy ID -> passage text for the policy set this process loaded."""
    return parse_passages(policies_path().read_text(encoding="utf-8"))


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


@dataclass(frozen=True)
class PolicyExpectation:
    """What a correctly built policy index contains."""

    version: str
    policy_ids: frozenset[str]


@lru_cache(maxsize=1)
def expectation() -> PolicyExpectation:
    text = policies_path().read_text(encoding="utf-8")
    return PolicyExpectation(policies_version(text), frozenset(parse_passages(text)))
