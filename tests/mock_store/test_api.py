import importlib
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.api.models import CustomerHistory, Order
from tests.mock_store.conftest import NOW, Scenarios

main = importlib.import_module("services.mock-store.main")
clock = importlib.import_module("services.mock-store.clock")
seed = importlib.import_module("services.mock-store.seed")


def test_responses_validate_against_the_contract(client: TestClient, seed_data: Any) -> None:
    for order in seed_data.orders:
        body = client.get(
            f"/orders/{order.order_id}", params={"email": order.customer_email}
        ).json()
        assert Order.model_validate(body) == order
    for history in seed_data.histories:
        body = client.get(f"/customers/{history.customer_email}/history").json()
        assert CustomerHistory.model_validate(body) == history


def test_nullable_fields_are_present_not_omitted(client: TestClient, scenarios: Scenarios) -> None:
    s = scenarios[6]
    body = client.get(f"/orders/{s.order_id}", params={"email": s.customer_email}).json()
    for name in (
        "dispatched_at",
        "delivered_at",
        "cancellation_record",
        "refund_record",
        "damage_report",
        "courier_tracking_number",
        "courier_eta",
    ):
        assert name in body
        assert body[name] is None


def test_timestamps_are_utc_with_z(client: TestClient, scenarios: Scenarios) -> None:
    s = scenarios[1]
    body = client.get(f"/orders/{s.order_id}", params={"email": s.customer_email}).json()
    assert body["placed_at"].endswith("Z")
    assert body["delivered_at"].endswith("Z")


def test_email_is_trimmed_and_case_insensitive(client: TestClient, scenarios: Scenarios) -> None:
    s = scenarios[1]
    response = client.get(
        f"/orders/{s.order_id}", params={"email": f"  {s.customer_email.upper()} "}
    )
    assert response.status_code == 200


def test_order_id_is_case_sensitive(client: TestClient, scenarios: Scenarios) -> None:
    s = scenarios[1]
    response = client.get(f"/orders/{str(s.order_id).lower()}", params={"email": s.customer_email})
    assert response.status_code == 404


def test_email_is_required(client: TestClient, scenarios: Scenarios) -> None:
    assert client.get(f"/orders/{scenarios[1].order_id}").status_code == 422


def test_unknown_customer_history_is_404(client: TestClient) -> None:
    assert client.get("/customers/nobody@example.com/history").status_code == 404


def test_history_counts_match_orders(client: TestClient, seed_data: Any) -> None:
    for history in seed_data.histories:
        owned = [o for o in seed_data.orders if o.customer_email == history.customer_email]
        assert history.total_orders == len(owned)
        assert history.last_order_id == max(owned, key=lambda o: o.placed_at).order_id


def test_cod_orders_never_exceed_the_limit(seed_data: Any) -> None:
    cod = [o for o in seed_data.orders if o.payment_method == "cod"]
    assert cod
    assert all(o.order_value_paise <= 500_000 for o in cod)


def test_seed_is_deterministic_for_a_fixed_clock() -> None:
    first = seed.build_seed(NOW).model_dump(mode="json")
    second = seed.build_seed(NOW).model_dump(mode="json")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_seed_moves_with_the_clock() -> None:
    later = datetime(2027, 3, 3, 4, 0, tzinfo=UTC)
    data = seed.build_seed(later)
    assert len(data.scenarios) == 31
    assert max(o.placed_at for o in data.orders) <= later


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_frozen_now_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FROZEN_NOW", "2026-10-15T12:00:00Z")
    assert clock.current_time() == NOW
    assert main.create_app() is not None


@pytest.mark.parametrize("bad", ["2026-10-15T12:00:00", "2026-10-15T17:30:00+05:30"])
def test_frozen_now_must_be_utc(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("FROZEN_NOW", bad)
    with pytest.raises(ValueError, match="UTC"):
        clock.current_time()


def test_blank_frozen_now_uses_real_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FROZEN_NOW", "")
    assert abs((datetime.now(UTC) - clock.current_time()).total_seconds()) < 5
