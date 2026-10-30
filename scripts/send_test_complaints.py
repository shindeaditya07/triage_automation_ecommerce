"""Send the test complaints to a deployed API one at a time, with a pause between them.

    uv run python -m scripts.send_test_complaints --api-url https://... --email you@example.com
    ... --interval 30            # seconds between complaints (default 30)
    ... --start 41 --count 10    # rows 41 to 50 only
    ... --run-id 20261030T1800   # carry on an earlier run: rows it already sent are skipped
    ... --dry-run                # print what would be sent, send nothing

It signs in as the owner (the password is asked for and never stored or printed) and sends each
row of docs/test-data/complaints-200.csv through the owner-only bulk endpoint, as a batch of one,
so the replies reach the model slowly enough not to open its circuit breaker. A row sent twice under
the same run id makes no second ticket. Exit code 0 when every row was accepted, 1 otherwise.
"""

import argparse
import csv
import getpass
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "docs" / "test-data" / "complaints-200.csv"
CSRF_HEADER = "x-csrf-token"
ALLOWED = {"name", "email", "order_id", "subject", "message"}
RETRIES = 3
RETRY_WAIT = 60.0  # seconds, after a 429 or a server error


def read_rows(path: Path = CASES) -> list[dict[str, str]]:
    """The complaints, with only the fields the endpoint takes; an empty order id is left out."""
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [{k: v for k, v in row.items() if v != ""} for row in rows]


def batch_id(run_id: str, row: int) -> UUID:
    """The same run and row always give the same id, so a row is never sent twice."""
    return uuid5(NAMESPACE_URL, f"replydesk-test-complaints/{run_id}/{row}")


class Session:
    """A signed-in owner: keeps the cookies and the CSRF token, and signs in again when needed."""

    def __init__(self, client: httpx.Client, api_url: str, email: str, password: str) -> None:
        self.client = client
        self.base = api_url.rstrip("/")
        self.email = email
        self.password = password
        self.csrf = ""

    def login(self) -> None:
        response = self.client.post(
            f"{self.base}/api/v1/auth/login",
            json={"email": self.email, "password": self.password},
            timeout=30,
        )
        if response.status_code != 200:
            raise SystemExit(f"Could not sign in: HTTP {response.status_code}")
        self.csrf = response.json()["csrf_token"]

    def send(self, row: dict[str, str], batch: UUID) -> httpx.Response:
        return self.client.post(
            f"{self.base}/api/v1/tickets/bulk",
            json={"batch_id": str(batch), "complaints": [row]},
            headers={CSRF_HEADER: self.csrf},
            timeout=60,
        )


def send_one(
    session: Session, row: dict[str, str], batch: UUID, sleep: Callable[[float], None]
) -> str | None:
    """Send one row; None when it was accepted, else a plain sentence about what went wrong."""
    problem = "could not stay signed in"
    for attempt in range(1, RETRIES + 1):
        try:
            response = session.send(row, batch)
        except httpx.HTTPError as exc:
            problem = f"no answer ({type(exc).__name__})"
        else:
            if response.status_code in (401, 403):
                session.login()  # the session ran out; sign in again and retry
                continue
            if response.status_code == 200:
                body = response.json()
                if body["rejected"]:
                    return f"rejected: {body['rejected'][0]['reason']}"
                return None
            problem = f"HTTP {response.status_code}"
            if response.status_code not in (429, 500, 502, 503, 504):
                return problem
        if attempt < RETRIES:
            sleep(RETRY_WAIT)
    return problem


def run(
    session: Session,
    rows: list[dict[str, str]],
    run_id: str,
    first: int,
    interval: float,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    """Send rows[first-1:] in order, waiting `interval` seconds after each; the problems found."""
    problems: list[str] = []
    session.login()
    total = len(rows)
    for number in range(first, total + 1):
        problem = send_one(session, rows[number - 1], batch_id(run_id, number), sleep)
        if problem:
            problems.append(f"row {number}: {problem}")
            print(f"FAIL row {number}/{total}: {problem}", flush=True)
        else:
            print(f"sent row {number}/{total}", flush=True)
        if number < total:
            sleep(interval)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--api-url", help="the API's base URL, no trailing path")
    parser.add_argument("--email", help="the owner's sign-in address")
    parser.add_argument("--interval", type=float, default=30.0, help="seconds between complaints")
    parser.add_argument("--start", type=int, default=1, help="first row to send (1 is the first)")
    parser.add_argument("--count", type=int, help="how many rows to send from --start")
    parser.add_argument("--run-id", help="a run to carry on; a new one is made by default")
    parser.add_argument("--dry-run", action="store_true", help="print what would be sent")
    args = parser.parse_args(argv)

    rows = read_rows()
    if args.start < 1 or args.start > len(rows):
        print(f"--start must be between 1 and {len(rows)}.", file=sys.stderr)
        return 1
    last = len(rows) if args.count is None else min(len(rows), args.start + args.count - 1)
    rows = rows[:last]
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    sending = last - args.start + 1
    minutes = round(sending * args.interval / 60)
    print(
        f"Run {run_id}: rows {args.start} to {last}, one every {args.interval:g}s (~{minutes} min)."
    )
    if args.dry_run:
        for number in range(args.start, last + 1):
            print(f"{number}: {rows[number - 1].get('email')} {rows[number - 1].get('subject')}")
        return 0
    if not args.api_url or not args.email:
        print("--api-url and --email are required unless you use --dry-run.", file=sys.stderr)
        return 1
    password = getpass.getpass(f"Password for {args.email}: ")
    with httpx.Client() as client:
        session = Session(client, args.api_url, args.email, password)
        problems = run(session, rows, run_id, args.start, args.interval)
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    print(f"Done. Run id {run_id}; to carry on after a stop use --run-id {run_id} --start N.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
