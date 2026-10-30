"""The slow sender for the test complaints: one row per request, paced, resumable, signed in."""

import json

import httpx

from scripts import send_test_complaints as sender

CSRF = "csrf-1"


def handler_for(log: list[httpx.Request], statuses: list[int] | None = None):  # type: ignore[no-untyped-def]
    queue = list(statuses or [])

    def handle(request: httpx.Request) -> httpx.Response:
        log.append(request)
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"csrf_token": CSRF, "access_expires_in": 900})
        status = queue.pop(0) if queue else 200
        if status == 200:
            return httpx.Response(200, json={"received": 1, "duplicates": 0, "rejected": []})
        return httpx.Response(status, json={"detail": "x"})

    return handle


def make(log: list[httpx.Request], statuses: list[int] | None = None) -> sender.Session:
    client = httpx.Client(transport=httpx.MockTransport(handler_for(log, statuses)))
    return sender.Session(client, "https://api.test/", "owner@example.com", "pw")


def test_the_committed_file_gives_200_rows_with_only_the_fields_the_endpoint_takes() -> None:
    rows = sender.read_rows()
    assert len(rows) == 200
    assert all(set(row) <= sender.ALLOWED for row in rows)


def test_each_row_is_its_own_request_with_the_csrf_header_and_a_pause_between() -> None:
    log: list[httpx.Request] = []
    sleeps: list[float] = []
    rows = [{"name": "A", "email": "a@x.com", "subject": "s", "message": "m"}] * 3

    problems = sender.run(make(log), rows, "run1", 1, 30, sleeps.append)

    posts = [r for r in log if r.url.path.endswith("/tickets/bulk")]
    assert problems == []
    assert len(posts) == 3
    assert sleeps == [30, 30]  # between rows, not after the last
    assert all(r.headers[sender.CSRF_HEADER] == CSRF for r in posts)
    assert all(len(json.loads(r.content)["complaints"]) == 1 for r in posts)
    assert len({json.loads(r.content)["batch_id"] for r in posts}) == 3


def test_a_row_always_gets_the_same_batch_id_in_the_same_run() -> None:
    assert sender.batch_id("r", 5) == sender.batch_id("r", 5)
    assert sender.batch_id("r", 5) != sender.batch_id("r", 6)
    assert sender.batch_id("r", 5) != sender.batch_id("other", 5)


def test_starting_part_way_skips_the_earlier_rows() -> None:
    log: list[httpx.Request] = []
    rows = [{"email": f"{n}@x.com", "message": "m"} for n in range(5)]

    sender.run(make(log), rows, "r", 4, 0, lambda _s: None)

    sent = [json.loads(r.content)["complaints"][0]["email"] for r in log if "bulk" in r.url.path]
    assert sent == ["3@x.com", "4@x.com"]


def test_a_busy_server_is_waited_out_and_tried_again() -> None:
    log: list[httpx.Request] = []
    sleeps: list[float] = []

    problem = sender.send_one(
        make(log, [429, 200]), {"message": "m"}, sender.batch_id("r", 1), sleeps.append
    )

    assert problem is None
    assert sleeps == [sender.RETRY_WAIT]


def test_an_expired_session_signs_in_again_and_the_row_is_sent() -> None:
    log: list[httpx.Request] = []

    problem = sender.send_one(
        make(log, [401, 200]), {"message": "m"}, sender.batch_id("r", 1), lambda _s: None
    )

    assert problem is None
    assert sum(r.url.path.endswith("/auth/login") for r in log) == 1


def test_a_refusal_is_reported_and_the_next_row_still_goes() -> None:
    log: list[httpx.Request] = []
    rows = [{"message": "m"}, {"message": "m"}]

    problems = sender.run(make(log, [400, 200]), rows, "r", 1, 0, lambda _s: None)

    assert problems == ["row 1: HTTP 400"]
    assert sum(r.url.path.endswith("/tickets/bulk") for r in log) == 2


def test_dry_run_sends_nothing_and_asks_for_no_password(capsys) -> None:  # type: ignore[no-untyped-def]
    assert sender.main(["--dry-run", "--start", "198"]) == 0
    out = capsys.readouterr().out
    assert "rows 198 to 200" in out
