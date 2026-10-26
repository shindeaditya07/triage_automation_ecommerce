"""Fail if policy_constants.yaml disagrees with the passages it cites in docs/policies.md."""

import sys
from pathlib import Path
from typing import Any

import yaml

from scripts.policy_lib import (
    CONSTANTS_PATH,
    POLICIES_PATH,
    expected_token,
    number_tokens,
    parse_passages,
    policies_version,
)

UNITS = {"days", "hours", "months", "paise", "count"}


def check(constants_path: Path = CONSTANTS_PATH, policies_path: Path = POLICIES_PATH) -> list[str]:
    errors: list[str] = []
    policies_text = policies_path.read_text(encoding="utf-8")
    passages = parse_passages(policies_text)
    data: dict[str, Any] = yaml.safe_load(constants_path.read_text(encoding="utf-8"))

    if str(data.get("policy_version")) != policies_version(policies_text):
        errors.append(
            f"policy_version {data.get('policy_version')!r} does not match "
            f"policies.md version {policies_version(policies_text)!r}"
        )

    seen: set[str] = set()
    for policy_id, constants in (data.get("constants") or {}).items():
        passage = passages.get(policy_id)
        if passage is None:
            errors.append(f"{policy_id}: no such passage in policies.md")
            continue
        for name, spec in constants.items():
            where = f"{policy_id}.{name}"
            if name in seen:
                errors.append(f"{where}: constant name is used more than once")
            seen.add(name)
            value, unit, text = spec.get("value"), spec.get("unit"), spec.get("text")
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(f"{where}: value must be an integer, got {value!r}")
                continue
            if unit not in UNITS:
                errors.append(f"{where}: unit must be one of {sorted(UNITS)}, got {unit!r}")
                continue
            if not text or text not in passage:
                errors.append(f"{where}: excerpt {text!r} is not in passage {policy_id}")
                continue
            try:
                token = expected_token(value, unit)
            except ValueError as exc:
                errors.append(f"{where}: {exc}")
                continue
            if token not in number_tokens(text):
                errors.append(f"{where}: value {value} ({token}) does not appear in {text!r}")
    return errors


def main() -> int:
    errors = check()
    for error in errors:
        print(f"FAIL {error}")
    if errors:
        return 1
    print("policy_constants.yaml agrees with docs/policies.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
