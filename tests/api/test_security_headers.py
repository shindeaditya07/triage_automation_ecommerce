"""The headers on every API response, errors and the interactive docs included."""

import pytest
from fastapi.testclient import TestClient

from services.api.main import create_app
from services.api.security_headers import API_CSP

THREE = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
}


@pytest.fixture
def client():  # type: ignore[no-untyped-def]
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.mark.parametrize("path", ["/healthz", "/api/v1/tickets", "/no/such/route"])
def test_json_responses_get_all_four_headers(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert {k: response.headers.get(k) for k in THREE} == THREE
    assert response.headers["content-security-policy"] == API_CSP


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_the_interactive_docs_keep_the_three_headers_but_not_the_api_csp(
    client: TestClient, path: str
) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert {k: response.headers.get(k) for k in THREE} == THREE
    assert "content-security-policy" not in response.headers


def test_the_api_csp_allows_nothing_and_forbids_framing() -> None:
    assert "default-src 'none'" in API_CSP
    assert "frame-ancestors 'none'" in API_CSP
    assert "unsafe-inline" not in API_CSP
