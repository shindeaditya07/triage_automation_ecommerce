"""Release smoke test against a deployed API: is it ready, and does signed intake work?

    SYNTHETIC_CHECK_HMAC_SECRET=... uv run python -m scripts.synthetic_check --api-url https://...

It checks three things and prints a line for each, never the secret:

1. `/readyz` answers 200 (database, pgvector, migrations and policy index are all fine). It is
   retried, because a freshly deployed service can take a few minutes to come up.
2. A signed email-webhook POST is accepted (201). This proves the deployed `HMAC_SECRET` matches the
   one given here, the database accepts a ticket, and intake works end to end.
3. The same message sent again is recognised as a duplicate (200, not a second ticket).

It does NOT wait for the ticket to be drafted: reading a ticket needs the owner's login, and this
check deliberately holds no owner credentials. The ticket it creates is real and shows in the
owner's queue under the subject "[synthetic check]"; reject it there.

Exit code 0 when all three pass, 1 otherwise.
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

KEY_ENV = "SYNTHETIC_CHECK_HMAC_SECRET"  # pragma: allowlist secret
READY_ATTEMPTS = 30
READY_WAIT_SECONDS = 10.0


def sign(key: str, body: bytes) -> str:
    return "sha256=" + hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


def payload(message_id: str, sent_at: datetime) -> bytes:
    return json.dumps(
        {
            "message_id": message_id,
            "received_at": sent_at.isoformat(),
            "customer": {"email": "synthetic-check@example.com", "name": "Synthetic Check"},
            "subject": "[synthetic check] release smoke test",
            "body": "Automated release check. Please ignore this message and reject its draft.",
        }
    ).encode()


def wait_until_ready(
    client: httpx.Client,
    api_url: str,
    attempts: int = READY_ATTEMPTS,
    wait: float = READY_WAIT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> str | None:
    """None when /readyz answers 200, otherwise what the last attempt saw."""
    seen = "no attempt made"
    for attempt in range(attempts):
        try:
            response = client.get(f"{api_url}/readyz", timeout=10)
            if response.status_code == 200:
                return None
            seen = f"/readyz answered {response.status_code}"
        except httpx.HTTPError as exc:
            seen = f"/readyz did not answer ({type(exc).__name__})"
        if attempt < attempts - 1:
            sleep(wait)
    return seen


def check(
    client: httpx.Client,
    api_url: str,
    key: str,
    now: datetime | None = None,
    attempts: int = READY_ATTEMPTS,
    wait: float = READY_WAIT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    """The problems found, as plain sentences; empty means every check passed."""
    api_url = api_url.rstrip("/")
    sent_at = now or datetime.now(UTC)
    problems: list[str] = []

    not_ready = wait_until_ready(client, api_url, attempts, wait, sleep)
    if not_ready:
        return [f"not ready: {not_ready}"]
    print("ok: /readyz is 200")

    body = payload(f"synthetic-check-{int(sent_at.timestamp())}@replydesk", sent_at)
    headers = {"content-type": "application/json", "x-signature": sign(key, body)}
    url = f"{api_url}/api/v1/tickets/email"
    for label, expected in (("signed intake", 201), ("duplicate of the same message", 200)):
        try:
            response = client.post(url, content=body, headers=headers, timeout=20)
        except httpx.HTTPError as exc:
            problems.append(f"{label}: no answer ({type(exc).__name__})")
            break
        if response.status_code != expected:
            hint = " (is the signing secret the same as the API's HMAC_SECRET?)"
            problems.append(
                f"{label}: expected {expected}, got {response.status_code}"
                + (hint if response.status_code == 401 else "")
            )
            break
        print(f"ok: {label} answered {expected}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--api-url", required=True, help="the API's base URL, no trailing path")
    args = parser.parse_args(argv)
    key = os.environ.get(KEY_ENV, "")
    if not key:
        print(f"{KEY_ENV} is not set", file=sys.stderr)
        return 1
    with httpx.Client() as client:
        problems = check(client, args.api_url, key)
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
