"""Send the 18 unlabeled review messages to a deployed API so you can work through them in the app.

    STAGING_HMAC_SECRET=... uv run python -m scripts.send_review_tickets --api-url https://...
    ... --only T906,T915          # just those messages
    ... --list                    # print what would be sent, send nothing

Each message goes to the signed email endpoint as the customer who owns the order it names, so
the pipeline sees what it would see for that customer. A message that is meant to be the third on
its thread (T911) is sent behind two short earlier messages, which also become tickets in the
queue. Message ids are new on every run, so a second run makes new tickets instead of repeats.

The secret is the API's HMAC_SECRET, read from the environment and never printed.
Exit code 0 when every message was accepted, 1 otherwise.
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

import httpx

from evals import corpus
from evals.review import load_unlabeled

KEY_ENV = "STAGING_HMAC_SECRET"  # pragma: allowlist secret
EARLIER_MESSAGE = "Following up on my order, I have not heard back."


def sign(key: str, body: bytes) -> str:
    return "sha256=" + hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


def messages_for(run_id: str, only: Sequence[str] | None = None) -> list[dict[str, object]]:
    """Every request body to send, in order, earlier thread messages first."""
    wanted = {name.strip().upper() for name in only or [] if name.strip()}
    bodies: list[dict[str, object]] = []
    for ticket in load_unlabeled():
        if wanted and ticket.id not in wanted:
            continue
        customer = {"email": ticket.customer_email, "name": ticket.customer_name}
        previous: str | None = None
        texts = [EARLIER_MESSAGE] * (ticket.thread_message_count - 1)
        texts.append(corpus.resolve(ticket.body, ticket.scenario_case))
        for number, text in enumerate(texts, start=1):
            message_id = f"<review-{run_id}-{ticket.id}-{number}@replydesk>"
            bodies.append(
                {
                    "message_id": message_id,
                    "customer": customer,
                    "subject": ticket.subject if number == len(texts) else "Re: my order",
                    "body": text,
                    "in_reply_to": previous,
                    "review_id": ticket.id,
                }
            )
            previous = message_id
    return bodies


def send(
    client: httpx.Client,
    api_url: str,
    key: str,
    bodies: Sequence[dict[str, object]],
    pause: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    """Post each body; the problems found as plain sentences, empty when all were accepted."""
    problems: list[str] = []
    url = f"{api_url.rstrip('/')}/api/v1/tickets/email"
    for body in bodies:
        review_id = body["review_id"]
        payload = {k: v for k, v in body.items() if k != "review_id"}
        raw = json.dumps(payload).encode()
        payload_headers = {"content-type": "application/json", "x-signature": sign(key, raw)}
        try:
            response = client.post(url, content=raw, headers=payload_headers, timeout=30)
        except httpx.HTTPError as exc:
            problems.append(f"{review_id}: no answer ({type(exc).__name__})")
            continue
        if response.status_code != 201:
            hint = " (is the secret the same as the API's HMAC_SECRET?)"
            problems.append(
                f"{review_id}: expected 201, got {response.status_code}"
                + (hint if response.status_code == 401 else "")
            )
            continue
        print(f"sent {review_id}: ticket {response.json().get('ticket_id')}")
        sleep(pause)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--api-url", help="the API's base URL, no trailing path")
    parser.add_argument("--only", help="comma-separated message ids, for example T906,T915")
    parser.add_argument("--list", action="store_true", help="print what would be sent")
    parser.add_argument("--pause", type=float, default=1.0, help="seconds between messages")
    args = parser.parse_args(argv)

    run_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    bodies = messages_for(run_id, args.only.split(",") if args.only else None)
    if not bodies:
        print("No message matches --only.", file=sys.stderr)
        return 1
    if args.list:
        for body in bodies:
            customer = body["customer"]
            email = customer["email"] if isinstance(customer, dict) else ""
            print(f"{body['review_id']} from {email}: {body['subject']}")
        return 0
    if not args.api_url:
        print("--api-url is required unless you use --list.", file=sys.stderr)
        return 1
    key = os.environ.get(KEY_ENV, "")
    if not key:
        print(f"{KEY_ENV} is not set", file=sys.stderr)
        return 1
    with httpx.Client() as client:
        problems = send(client, args.api_url, key, bodies, args.pause)
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
