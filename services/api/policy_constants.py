"""Runtime access to policy_constants.yaml, the single source for numeric policy values.

`make policy-check` proves each value agrees with the passage it cites in docs/policies.md; code
reads the numbers from here and never hard-codes them (docs/AGENTS.md section 1).
"""

import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "policy_constants.yaml"


@lru_cache(maxsize=1)
def constants() -> Mapping[str, int]:
    """Constant name -> value, for example `constants()["damage_window_hours"]` is 48."""
    path = Path(os.environ.get("POLICY_CONSTANTS_PATH") or DEFAULT_PATH)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    flat: dict[str, int] = {}
    for policy_id, entries in document["constants"].items():
        for name, entry in entries.items():
            if name in flat:
                raise ValueError(f"constant {name!r} is defined twice (again under {policy_id})")
            flat[name] = int(entry["value"])
    return flat


def constant(name: str) -> int:
    try:
        return constants()[name]
    except KeyError:
        raise KeyError(f"no policy constant named {name!r} in policy_constants.yaml") from None
