"""Fail if docs/policies.md changed without a version bump and a refreshed .policy_lock.

Usage:
    python -m scripts.check_policy_sha            check
    python -m scripts.check_policy_sha --update   rewrite .policy_lock (after bumping the version
                                                  and re-running the evals)
"""

import json
import sys
from pathlib import Path

from scripts.policy_lib import LOCK_PATH, POLICIES_PATH, policies_sha256, policies_version


def check(policies_path: Path = POLICIES_PATH, lock_path: Path = LOCK_PATH) -> list[str]:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    version = policies_version(policies_path.read_text(encoding="utf-8"))
    sha = policies_sha256(policies_path)
    if sha == lock["sha256"] and version == lock["version"]:
        return []
    if sha != lock["sha256"] and version == lock["version"]:
        return [
            f"policies.md changed but the version is still {version}. Bump the version, re-run "
            "the evals, then run: python -m scripts.check_policy_sha --update"
        ]
    return [
        f"policies.md is version {version} (sha {sha[:12]}) but .policy_lock says version "
        f"{lock['version']} (sha {lock['sha256'][:12]}). Re-run the evals, then run: "
        "python -m scripts.check_policy_sha --update"
    ]


def update(policies_path: Path = POLICIES_PATH, lock_path: Path = LOCK_PATH) -> None:
    version = policies_version(policies_path.read_text(encoding="utf-8"))
    lock = {
        "file": "docs/policies.md",
        "version": version,
        "sha256": policies_sha256(policies_path),
    }
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    if "--update" in sys.argv[1:]:
        update()
        print("Updated .policy_lock")
        return 0
    errors = check()
    for error in errors:
        print(f"FAIL {error}")
    if errors:
        return 1
    print("policies.md matches .policy_lock")
    return 0


if __name__ == "__main__":
    sys.exit(main())
