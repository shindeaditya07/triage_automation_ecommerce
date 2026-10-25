import json
from datetime import UTC, datetime

import httpx
import pytest

from scripts.synthetic_check import check, main, payload, sign
from services.api.intake.payloads import EmailPayload
from services.api.intake.routes import SIGNATURE_HEADER, _signature_matches

SECRET = "s3cret-value"  # pragma: allowlist secret
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


def server(
    ready: list[int] | None = None, intake: list[int] | None = None, secret: str = SECRET
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """A stand-in API: /readyz answers from `ready`, intake from `intake` once the signature
    verifies with the same function the real API uses (a wrong secret gets 401)."""
    ready_answers = list(ready or [200])
    intake_answers = list(intake or [201, 200])
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/readyz":
            status = ready_answers.pop(0) if len(ready_answers) > 1 else ready_answers[0]
            return httpx.Response(status)
        header = request.headers.get(SIGNATURE_HEADER)
        if not _signature_matches(secret.encode(), request.content, header):
            return httpx.Response(401)
        return httpx.Response(intake_answers.pop(0), json={"ticket_id": "t"})

    return httpx.MockTransport(handle), seen


def run(transport: httpx.MockTransport, **kwargs: object) -> list[str]:
    with client(transport) as c:
        return check(c, "https://api.example", SECRET, NOW, sleep=lambda _: None, **kwargs)  # type: ignore[arg-type]


def test_everything_passes_against_a_healthy_api() -> None:
    transport, seen = server()
    assert run(transport) == []
    assert [r.url.path for r in seen] == [
        "/readyz",
        "/api/v1/tickets/email",
        "/api/v1/tickets/email",
    ]


def test_the_payload_is_one_the_real_api_accepts() -> None:
    parsed = EmailPayload.model_validate_json(payload("m-1@replydesk", NOW))
    assert parsed.subject is not None and parsed.subject.startswith("[synthetic check]")


def test_the_signature_is_the_one_the_api_checks() -> None:
    body = payload("m-1@replydesk", NOW)
    assert _signature_matches(SECRET.encode(), body, sign(SECRET, body))
    assert not _signature_matches(b"another", body, sign(SECRET, body))


def test_it_waits_for_a_service_that_is_still_starting() -> None:
    transport, seen = server(ready=[503, 503, 200])
    assert run(transport) == []
    assert sum(r.url.path == "/readyz" for r in seen) == 3


def test_a_service_that_never_gets_ready_fails_without_sending_a_ticket() -> None:
    transport, seen = server(ready=[503])
    problems = run(transport, attempts=3)
    assert problems == ["not ready: /readyz answered 503"]
    assert all(r.url.path == "/readyz" for r in seen)


def test_a_wrong_signing_secret_says_so() -> None:
    transport, _ = server(secret="the-api-has-another")
    [problem] = run(transport)
    assert "expected 201, got 401" in problem and "HMAC_SECRET" in problem


def test_a_duplicate_that_makes_a_second_ticket_fails() -> None:
    transport, _ = server(intake=[201, 201])
    [problem] = run(transport)
    assert "duplicate" in problem and "expected 200, got 201" in problem


def test_main_needs_the_secret_and_never_prints_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SYNTHETIC_CHECK_HMAC_SECRET", raising=False)
    assert main(["--api-url", "https://api.example"]) == 1
    assert "SYNTHETIC_CHECK_HMAC_SECRET is not set" in capsys.readouterr().err


def test_the_body_it_signs_is_json() -> None:
    assert json.loads(payload("m@x", NOW))["customer"]["email"] == "synthetic-check@example.com"
