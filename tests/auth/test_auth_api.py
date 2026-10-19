from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.api.auth.store import InMemoryAuthStore
from services.api.main import create_app
from services.api.settings import AuthSettings
from tests.auth.conftest import (
    OWNER_EMAIL,
    OWNER_PASSWORD,
    WRONG_PASSWORD,
    FakeClock,
    csrf,
    login,
)

MakeClient = Callable[..., TestClient]
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"


def _set_cookie_headers(response: Any) -> list[str]:
    return response.headers.get_list("set-cookie")


def _cookie(response: Any, name: str) -> str:
    return next(h for h in _set_cookie_headers(response) if h.startswith(f"{name}="))


# --- login ------------------------------------------------------------------------------------


def test_login_sets_three_cookies_with_the_documented_attributes(client: TestClient) -> None:
    response = login(client)
    assert response.status_code == 200
    body = response.json()
    assert body["access_expires_in"] == 900
    assert body["csrf_token"] == client.cookies.get("rd_csrf")
    assert response.headers["cache-control"] == "no-store"

    access, refresh, token = (
        _cookie(response, n).lower() for n in ("rd_access", "rd_refresh", "rd_csrf")
    )
    for cookie in (access, refresh, token):
        assert "samesite=strict" in cookie
    assert "httponly" in access and "httponly" in refresh
    assert "httponly" not in token  # the page must be able to read it
    assert "path=/api;" in access
    assert "path=/api/v1/auth;" in refresh
    assert "max-age=900" in access
    assert "max-age=604800" in refresh


def test_cookies_are_secure_in_deployed_environments(make_client: MakeClient) -> None:
    response = login(make_client(environment="staging"))
    for name in ("rd_access", "rd_refresh", "rd_csrf"):
        assert "secure" in _cookie(response, name).lower()


def test_the_session_tokens_are_not_in_the_response_body(client: TestClient) -> None:
    response = login(client)
    assert client.cookies.get("rd_access") not in response.text
    assert client.cookies.get("rd_refresh") not in response.text


def test_login_then_me_returns_the_owner(client: TestClient) -> None:
    login(client)
    response = client.get(ME)
    assert response.status_code == 200
    assert response.json() == {"email": OWNER_EMAIL}


def test_email_is_case_and_whitespace_insensitive(client: TestClient) -> None:
    assert login(client, email="  Owner@Example.COM ").status_code == 200


@pytest.mark.parametrize(
    ("email", "password"),
    [(OWNER_EMAIL, "wrong password"), ("someone@else.com", OWNER_PASSWORD), ("x@y.z", "nope")],
)
def test_bad_credentials_get_the_same_generic_401(
    client: TestClient, email: str, password: str
) -> None:
    response = login(client, email=email, password=password)
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid credentials"}
    assert not _set_cookie_headers(response)


def test_wrong_email_still_runs_a_password_verification(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.api.auth import service

    seen: list[str] = []
    real = service.verify_password

    async def spy(stored: str, password: str) -> bool:
        seen.append(stored)
        return await real(stored, password)

    monkeypatch.setattr(service, "verify_password", spy)
    login(client, email="nobody@else.com")
    assert len(seen) == 1 and seen[0] == service.dummy_hash()


@pytest.mark.parametrize(
    "payload",
    [{}, {"email": OWNER_EMAIL}, {"email": "", "password": "x"}, {"email": "a", "password": ""},
     {"email": "a", "password": "x" * 1025}, {"email": "a", "password": "x", "extra": 1}],
)  # fmt: skip
def test_malformed_login_bodies_are_rejected_before_any_hashing(
    client: TestClient, payload: dict[str, Any]
) -> None:
    assert client.post(LOGIN, json=payload).status_code == 422


# --- lockout and rate limit -------------------------------------------------------------------


def test_the_account_locks_after_n_consecutive_failures_even_for_the_right_password(
    make_client: MakeClient, clock: FakeClock
) -> None:
    client = make_client(login_max_failures=3, login_ip_max_attempts=100)
    for _ in range(3):
        assert login(client, password=WRONG_PASSWORD).status_code == 401
    locked = login(client)  # correct password, but locked
    assert locked.status_code == 429
    assert locked.json() == {"detail": "too many login attempts"}
    assert locked.headers["retry-after"] == "60"
    clock.advance(seconds=61)
    assert login(client).status_code == 200


def test_a_successful_login_resets_the_count(make_client: MakeClient) -> None:
    client = make_client(login_max_failures=3, login_ip_max_attempts=100)
    for _ in range(2):
        login(client, password=WRONG_PASSWORD)
    assert login(client).status_code == 200
    for _ in range(2):
        assert login(client, password=WRONG_PASSWORD).status_code == 401
    assert login(client).status_code == 200  # never reached three in a row


def test_repeated_failures_after_the_lock_expires_back_off_exponentially(
    make_client: MakeClient, clock: FakeClock
) -> None:
    client = make_client(login_max_failures=2, login_ip_max_attempts=100)
    login(client, password=WRONG_PASSWORD)
    login(client, password=WRONG_PASSWORD)  # locked for 60 s
    clock.advance(seconds=61)
    assert login(client, password=WRONG_PASSWORD).status_code == 401  # lock doubles to 120 s
    assert login(client).headers["retry-after"] == "120"


def test_the_lockout_survives_a_new_app_instance_sharing_the_store(
    make_client: MakeClient, make_settings: Callable[..., AuthSettings], clock: FakeClock,
    store: InMemoryAuthStore,
) -> None:  # fmt: skip
    first = make_client(login_max_failures=2, login_ip_max_attempts=100)
    login(first, password=WRONG_PASSWORD)
    login(first, password=WRONG_PASSWORD)
    restarted = TestClient(
        create_app(auth_settings=make_settings(login_max_failures=2), auth_store=store, clock=clock)
    )
    with restarted:
        assert login(restarted).status_code == 429  # a restart does not clear a lock


def test_one_address_is_rate_limited_before_the_account_is_touched(
    make_client: MakeClient, store: InMemoryAuthStore
) -> None:
    client = make_client(login_ip_max_attempts=2, login_max_failures=100)
    login(client, password=WRONG_PASSWORD)
    login(client, password=WRONG_PASSWORD)
    limited = login(client, password=WRONG_PASSWORD)
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0
    assert store.lockout_state().consecutive_failures == 2  # the third never reached the store


def test_a_spoofed_forwarded_for_header_does_not_dodge_the_limit_by_default(
    make_client: MakeClient,
) -> None:
    client = make_client(login_ip_max_attempts=1, login_max_failures=100)
    login(client, password=WRONG_PASSWORD)
    spoofed = client.post(
        LOGIN,
        json={"email": OWNER_EMAIL, "password": WRONG_PASSWORD},
        headers={"X-Forwarded-For": "9.9.9.9"},
    )
    assert spoofed.status_code == 429


def test_behind_a_trusted_proxy_each_real_client_gets_its_own_allowance(
    make_client: MakeClient,
) -> None:
    client = make_client(login_ip_max_attempts=1, login_max_failures=100, trusted_proxy_hops=1)

    def attempt(real_ip: str, claimed: str = "6.6.6.6") -> int:
        return client.post(
            LOGIN,
            json={"email": OWNER_EMAIL, "password": WRONG_PASSWORD},
            headers={"X-Forwarded-For": f"{claimed}, {real_ip}"},
        ).status_code

    assert attempt("203.0.113.1") == 401
    assert attempt("203.0.113.1", claimed="7.7.7.7") == 429  # changing the claimed IP does not help
    assert attempt("203.0.113.2") == 401


# --- sessions: refresh and logout -------------------------------------------------------------


def test_access_token_expires_and_refresh_issues_a_new_one(
    client: TestClient, clock: FakeClock
) -> None:
    login(client)
    clock.advance(minutes=16)
    assert client.get(ME).status_code == 401
    response = client.post(REFRESH, headers=csrf(client))
    assert response.status_code == 200
    assert client.get(ME).status_code == 200


def test_refresh_rotates_the_refresh_token_and_keeps_the_csrf_token(client: TestClient) -> None:
    login(client)
    before = (client.cookies.get("rd_refresh"), client.cookies.get("rd_csrf"))
    client.post(REFRESH, headers=csrf(client))
    after = (client.cookies.get("rd_refresh"), client.cookies.get("rd_csrf"))
    assert after[0] != before[0]
    assert after[1] == before[1]


def test_a_refresh_token_used_twice_ends_the_whole_session(client: TestClient) -> None:
    login(client)
    old_refresh = client.cookies.get("rd_refresh")
    headers = csrf(client)
    assert client.post(REFRESH, headers=headers).status_code == 200
    new_refresh = client.cookies.get("rd_refresh")

    client.cookies.set("rd_refresh", old_refresh, path="/api/v1/auth")  # replay the stolen one
    assert client.post(REFRESH, headers=headers).status_code == 401

    client.cookies.set("rd_refresh", new_refresh, path="/api/v1/auth")  # even the new one is dead
    client.cookies.set("rd_csrf", headers["X-CSRF-Token"], path="/")  # the 401 cleared it
    assert client.post(REFRESH, headers=headers).status_code == 401


def test_an_expired_refresh_token_is_refused_and_cookies_are_cleared(
    client: TestClient, clock: FakeClock
) -> None:
    login(client)
    headers = csrf(client)
    clock.advance(days=8)
    response = client.post(REFRESH, headers=headers)
    assert response.status_code == 401
    assert 'rd_refresh=""' in _cookie(response, "rd_refresh") or "Max-Age=0" in _cookie(
        response, "rd_refresh"
    )


def test_refresh_without_a_cookie_is_401(client: TestClient) -> None:
    assert client.post(REFRESH).status_code == 401


def test_an_access_token_cannot_be_used_to_refresh(client: TestClient) -> None:
    login(client)
    client.cookies.set("rd_refresh", client.cookies.get("rd_access"), path="/api/v1/auth")
    assert client.post(REFRESH, headers=csrf(client)).status_code == 401


def test_logout_clears_cookies_and_revokes_the_refresh_token(client: TestClient) -> None:
    login(client)
    refresh_token = client.cookies.get("rd_refresh")
    headers = csrf(client)
    response = client.post(LOGOUT, headers=headers)
    assert response.status_code == 204
    for name in ("rd_access", "rd_refresh", "rd_csrf"):
        assert "max-age=0" in _cookie(response, name).lower()
    assert client.get(ME).status_code == 401

    client.cookies.set("rd_refresh", refresh_token, path="/api/v1/auth")  # replay after logout
    client.cookies.set("rd_csrf", headers["X-CSRF-Token"], path="/")  # a thief has every cookie
    assert client.post(REFRESH, headers=headers).status_code == 401


def test_logout_without_a_session_is_a_harmless_204(client: TestClient) -> None:
    assert client.post(LOGOUT).status_code == 204


# --- protected routes -------------------------------------------------------------------------


def test_me_needs_a_session(client: TestClient) -> None:
    assert client.get(ME).status_code == 401


def test_a_refresh_token_is_not_accepted_as_an_access_token(client: TestClient) -> None:
    login(client)
    client.cookies.set("rd_access", client.cookies.get("rd_refresh"), path="/api")
    assert client.get(ME).status_code == 401


def test_a_tampered_access_token_is_rejected(client: TestClient) -> None:
    login(client)
    token = client.cookies.get("rd_access") or ""
    client.cookies.set(
        "rd_access", token[:-2] + ("AA" if token[-2:] != "AA" else "BB"), path="/api"
    )
    assert client.get(ME).status_code == 401


# --- not configured ---------------------------------------------------------------------------


def test_without_auth_settings_the_app_starts_and_auth_answers_503(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    for name in ("SECRET_KEY", "OWNER_EMAIL", "OWNER_PASSWORD_HASH", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    with TestClient(create_app()) as client:
        assert client.get("/healthz").status_code == 200
        assert client.post(LOGIN, json={"email": "a", "password": "b"}).status_code == 503
        assert client.get(ME).status_code == 503
        assert client.post("/api/v1/auth/logout").status_code == 503


def test_no_owner_configured_means_no_login(make_client: MakeClient) -> None:
    client = make_client(owner_email="", owner_password_hash="")
    response = login(client)
    assert response.status_code == 503
    assert response.json() == {"detail": "authentication is not available"}


def test_an_unreadable_owner_hash_means_no_login_rather_than_a_crash(
    make_client: MakeClient,
) -> None:
    client = make_client(owner_password_hash="plaintext-by-mistake")
    assert login(client).status_code == 503


def test_without_a_database_login_is_503_not_500(
    make_settings: Callable[..., AuthSettings], clock: FakeClock, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:  # fmt: skip
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    with TestClient(create_app(auth_settings=make_settings(), clock=clock)) as client:
        assert login(client).status_code == 503
        assert client.get("/healthz").status_code == 200


def test_a_store_that_raises_a_database_error_gives_503_without_driver_text(
    make_settings: Callable[..., AuthSettings], clock: FakeClock
) -> None:
    from sqlalchemy.exc import OperationalError

    class Broken(InMemoryAuthStore):
        async def begin_login_attempt(self, *_: object) -> int:
            raise OperationalError(
                "SELECT secret FROM somewhere", {}, Exception("host=db.internal")
            )

    app = create_app(auth_settings=make_settings(), auth_store=Broken(), clock=clock)
    with TestClient(app) as client:
        response = login(client)
    assert response.status_code == 503
    assert response.json() == {"detail": "service unavailable"}
    assert "internal" not in response.text and "SELECT" not in response.text


def test_logout_still_clears_cookies_but_says_so_when_the_server_cannot_revoke(
    make_settings: Callable[..., AuthSettings], clock: FakeClock
) -> None:
    class FailsToRevoke(InMemoryAuthStore):
        broken = False

        async def revoke_session(self, *args: Any) -> None:
            if self.broken:
                raise OSError("db went away")
            await super().revoke_session(*args)

    store = FailsToRevoke()
    app = create_app(auth_settings=make_settings(), auth_store=store, clock=clock)
    with TestClient(app) as client:
        login(client)
        store.broken = True
        response = client.post(LOGOUT, headers=csrf(client))
    assert response.status_code == 503
    assert "db went away" not in response.text
    for name in ("rd_access", "rd_refresh", "rd_csrf"):
        assert "max-age=0" in _cookie(response, name).lower()


def test_refresh_during_an_outage_is_503_and_keeps_the_cookies(
    make_settings: Callable[..., AuthSettings], clock: FakeClock
) -> None:
    class Down(InMemoryAuthStore):
        down = False

        async def rotate_refresh(self, *args: Any) -> Any:
            if self.down:
                raise OSError("db went away")
            return await super().rotate_refresh(*args)

    store = Down()
    app = create_app(auth_settings=make_settings(), auth_store=store, clock=clock)
    with TestClient(app) as client:
        login(client)
        store.down = True
        response = client.post(REFRESH, headers=csrf(client))
        assert response.status_code == 503
        assert not _set_cookie_headers(
            response
        )  # the session may still be good once the db is back
        assert client.cookies.get("rd_refresh")


def test_logout_without_any_store_is_a_204(
    make_settings: Callable[..., AuthSettings], clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:  # fmt: skip
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    with TestClient(create_app(auth_settings=make_settings(), clock=clock)) as client:
        assert client.post(LOGOUT).status_code == 204
