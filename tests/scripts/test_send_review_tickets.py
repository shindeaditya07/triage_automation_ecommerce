import json

import httpx
import pytest

from scripts.send_review_tickets import main, messages_for, send
from services.api.intake.payloads import EmailPayload
from services.api.intake.routes import SIGNATURE_HEADER, _signature_matches

SECRET = "s3cret-value"  # pragma: allowlist secret


def server(secret: str = SECRET, status: int = 201) -> tuple[httpx.Client, list[dict[str, object]]]:
    seen: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if not _signature_matches(
            secret.encode(), request.content, request.headers.get(SIGNATURE_HEADER)
        ):
            return httpx.Response(401)
        seen.append(json.loads(request.content))
        return httpx.Response(status, json={"ticket_id": f"t{len(seen)}"})

    return httpx.Client(transport=httpx.MockTransport(handle)), seen


def test_every_body_is_a_valid_email_payload_for_the_customer_who_owns_the_order() -> None:
    bodies = messages_for("r1")
    assert {b["review_id"] for b in bodies} >= {"T901", "T915", "T918"}
    for body in bodies:
        payload = {k: v for k, v in body.items() if k != "review_id"}
        EmailPayload.model_validate(payload)
        assert "{order_id}" not in str(payload["body"])


def test_the_third_message_on_a_thread_is_sent_behind_two_earlier_ones() -> None:
    thread = [b for b in messages_for("r1") if b["review_id"] == "T911"]
    assert len(thread) == 3
    assert thread[0]["in_reply_to"] is None
    assert thread[1]["in_reply_to"] == thread[0]["message_id"]
    assert thread[2]["in_reply_to"] == thread[1]["message_id"]
    assert thread[2]["subject"] == "Third message"


def test_only_picks_messages_and_message_ids_change_per_run() -> None:
    only = messages_for("r1", ["t906", " T915 "])
    assert [b["review_id"] for b in only] == ["T906", "T915"]
    assert only[0]["message_id"] != messages_for("r2", ["T906"])[0]["message_id"]


def test_send_signs_each_body_and_reports_success() -> None:
    client, seen = server()
    problems = send(
        client, "https://api.example", SECRET, messages_for("r1", ["T906"]), 0, lambda _: None
    )
    assert problems == [] and len(seen) == 1 and "review_id" not in seen[0]


def test_a_wrong_secret_is_reported_with_a_hint() -> None:
    client, _ = server(secret="other-secret-value")  # pragma: allowlist secret
    problems = send(
        client, "https://api.example", SECRET, messages_for("r1", ["T906"]), 0, lambda _: None
    )
    assert len(problems) == 1 and "401" in problems[0] and "HMAC_SECRET" in problems[0]


def test_list_sends_nothing_and_needs_no_secret(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("STAGING_HMAC_SECRET", raising=False)
    assert main(["--list", "--only", "T915"]) == 0
    assert "T915 from deepak.sinha@example.com" in capsys.readouterr().out


def test_sending_without_a_secret_or_url_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAGING_HMAC_SECRET", raising=False)
    assert main(["--only", "T906"]) == 1
    assert main(["--api-url", "https://api.example", "--only", "T906"]) == 1
