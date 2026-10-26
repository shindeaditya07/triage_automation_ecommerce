import importlib

from fastapi.testclient import TestClient

# The directory is named "mock-store" (per the build plan), so it cannot be imported with
# a plain `import` statement.
app = importlib.import_module("services.mock-store.main").app


def test_health_returns_ok() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
