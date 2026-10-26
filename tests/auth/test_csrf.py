from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from services.api.auth.routes import require_owner
from services.api.auth.service import Owner
from services.api.main import create_app
from tests.auth.conftest import OWNER_EMAIL, FakeClock, csrf, login

REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"


@pytest.fixture
def app_with_routes(make_settings: Any, clock: FakeClock, store: Any) -> FastAPI:
    """The real app plus two owner-only mutations, as later branches will add."""
    app = create_app(auth_settings=make_settings(), auth_store=store, clock=clock)

    @app.post("/api/v1/things")
    def create_thing(owner: Owner = Depends(require_owner)) -> dict[str, str]:  # noqa: B008
        return {"made_by": owner.email}

    @app.delete("/api/v1/things/1")
    def delete_thing(owner: Owner = Depends(require_owner)) -> dict[str, str]:  # noqa: B008
        return {"deleted_by": owner.email}

    @app.post("/public/form")
    def public_form() -> dict[str, str]:
        return {"ok": "yes"}

    return app


@pytest.fixture
def client(app_with_routes: FastAPI) -> TestClient:
    with TestClient(app_with_routes) as test_client:
        login(test_client)
        return test_client


def test_a_mutation_without_the_header_is_403_and_never_runs(client: TestClient) -> None:
    response = client.post("/api/v1/things")
    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF token missing or invalid"}


def test_a_mutation_with_the_right_header_runs(client: TestClient) -> None:
    response = client.post("/api/v1/things", headers=csrf(client))
    assert response.status_code == 200
    assert response.json() == {"made_by": OWNER_EMAIL}


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_every_unsafe_method_is_checked(client: TestClient, method: str) -> None:
    assert client.request(method, "/api/v1/things/1").status_code == 403
    if method == "DELETE":
        assert client.request(method, "/api/v1/things/1", headers=csrf(client)).status_code == 200


def test_reads_do_not_need_the_header(client: TestClient) -> None:
    assert client.get("/api/v1/auth/me").status_code == 200


def test_a_header_that_differs_from_the_cookie_is_rejected(client: TestClient) -> None:
    assert client.post("/api/v1/things", headers={"X-CSRF-Token": "forged"}).status_code == 403


def test_a_matching_header_and_cookie_that_are_not_this_sessions_token_are_rejected(
    client: TestClient,
) -> None:
    """Cookie injection: the attacker plants their own value in both places."""
    client.cookies.set("rd_csrf", "attacker-value", path="/")
    assert (
        client.post("/api/v1/things", headers={"X-CSRF-Token": "attacker-value"}).status_code == 403
    )


def test_the_header_alone_without_the_cookie_is_rejected(client: TestClient) -> None:
    token = client.cookies.get("rd_csrf") or ""
    client.cookies.delete("rd_csrf", path="/")
    assert client.post("/api/v1/things", headers={"X-CSRF-Token": token}).status_code == 403


def test_another_sessions_token_is_rejected(app_with_routes: FastAPI) -> None:
    with TestClient(app_with_routes) as first, TestClient(app_with_routes) as second:
        login(first)
        login(second)
        stolen = csrf(second)
        first.cookies.set("rd_csrf", stolen["X-CSRF-Token"], path="/")
        assert first.post("/api/v1/things", headers=stolen).status_code == 403


def test_refresh_and_logout_are_protected_too(client: TestClient) -> None:
    assert client.post(REFRESH).status_code == 403
    assert client.post(LOGOUT).status_code == 403
    assert client.post(REFRESH, headers=csrf(client)).status_code == 200
    assert client.post(LOGOUT, headers=csrf(client)).status_code == 204


def test_login_itself_needs_no_token_even_with_stale_cookies(client: TestClient) -> None:
    assert login(client).status_code == 200


def test_the_token_survives_a_refresh_so_the_page_keeps_working(
    client: TestClient, clock: FakeClock
) -> None:
    headers = csrf(client)
    clock.advance(minutes=20)
    assert client.post(REFRESH, headers=headers).status_code == 200
    assert client.post("/api/v1/things", headers=headers).status_code == 200


def test_an_expired_session_cookie_still_demands_the_token(
    client: TestClient, clock: FakeClock
) -> None:
    clock.advance(minutes=20)
    assert client.post("/api/v1/things").status_code == 403  # not a 401: CSRF is checked first


def test_requests_with_no_session_cookies_are_left_to_their_own_authentication(
    app_with_routes: FastAPI,
) -> None:
    with TestClient(app_with_routes) as anonymous:
        assert anonymous.post("/api/v1/things").status_code == 401
        assert anonymous.post("/public/form").status_code == 200


def test_junk_cookies_do_not_authenticate_so_they_do_not_trigger_the_check(
    app_with_routes: FastAPI,
) -> None:
    with TestClient(app_with_routes) as anonymous:
        anonymous.cookies.set("rd_access", "junk", path="/api")
        anonymous.cookies.set("rd_refresh", "junk", path="/api/v1/auth")
        assert anonymous.post("/api/v1/things").status_code == 401


def test_paths_outside_api_are_not_checked(client: TestClient) -> None:
    assert client.post("/public/form").status_code == 200
