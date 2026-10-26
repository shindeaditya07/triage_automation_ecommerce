"""One test per required edge case (spec section 17), against the frozen clock 2026-10-15T12:00Z.

Policy windows are recomputed here from the contract fields on purpose: the test is the check that
the seeded dates really sit where the case says they do.
"""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient

from services.api.policy_constants import constant
from tests.mock_store.conftest import NOW, Scenarios, hours_between, ist_date, parse

Fetch = Callable[[int], dict[str, Any]]
TODAY = ist_date(NOW)


def return_window_end(order: dict[str, Any]) -> Any:
    """Last IST calendar day on which a return may start (RET-1.1: delivery date + 7, inclusive)."""
    return ist_date(order["delivered_at"]) + timedelta(days=constant("return_window_days"))


def flags(order: dict[str, Any]) -> set[str]:
    return {flag for item in order["items"] for flag in item["item_flags"]}


def test_all_31_cases_are_seeded_once(scenarios: Scenarios) -> None:
    assert sorted(scenarios) == list(range(1, 32))


def test_01_delivered_inside_return_window(order_for: Fetch) -> None:
    order = order_for(1)
    assert order["status"] == "delivered"
    assert return_window_end(order) >= TODAY


def test_02_return_window_expired_by_exactly_one_day(order_for: Fetch) -> None:
    order = order_for(2)
    assert order["status"] == "delivered"
    assert TODAY - return_window_end(order) == timedelta(days=1)


def test_03_in_transit_within_estimate(order_for: Fetch) -> None:
    order = order_for(3)
    assert order["status"] == "dispatched"
    assert order["delivered_at"] is None
    assert order["courier_eta"] is None
    assert (
        TODAY - ist_date(order["dispatched_at"])
    ).days < 5  # shorter than the shortest zone estimate


def test_04_late_without_courier_eta(order_for: Fetch) -> None:
    order = order_for(4)
    assert order["status"] == "dispatched"
    assert order["courier_eta"] is None
    # More than the longest standard-zone estimate, even counting every day as a business day.
    assert (TODAY - ist_date(order["dispatched_at"])).days > 8


def test_05_late_with_new_courier_eta(order_for: Fetch) -> None:
    order = order_for(5)
    assert order["status"] == "dispatched"
    assert (TODAY - ist_date(order["dispatched_at"])).days > 8
    assert parse(order["courier_eta"]) > NOW


def test_06_not_yet_shipped(order_for: Fetch) -> None:
    order = order_for(6)
    assert order["status"] == "placed"
    assert order["dispatched_at"] is None
    assert order["courier_tracking_number"] is None


def test_07_already_cancelled(order_for: Fetch) -> None:
    order = order_for(7)
    assert order["status"] == "cancelled"
    assert order["cancellation_record"]["cancelled_by"] == "customer"
    assert order["dispatched_at"] is None


def test_08_damage_inside_window_with_photo(order_for: Fetch) -> None:
    order = order_for(8)
    report = order["damage_report"]
    assert report["has_photo"] is True
    assert hours_between(report["reported_at"], order["delivered_at"]) <= constant(
        "damage_window_hours"
    )


def test_09_damage_without_photo(order_for: Fetch) -> None:
    order = order_for(9)
    report = order["damage_report"]
    assert report["has_photo"] is False
    assert hours_between(report["reported_at"], order["delivered_at"]) <= constant(
        "damage_window_hours"
    )


def test_10_damage_after_48_hours(order_for: Fetch) -> None:
    order = order_for(10)
    report = order["damage_report"]
    assert hours_between(report["reported_at"], order["delivered_at"]) > constant(
        "damage_window_hours"
    )


def test_11_final_sale_item(order_for: Fetch) -> None:
    order = order_for(11)
    assert flags(order) == {"final_sale"}
    assert order["damage_report"] is None
    assert return_window_end(order) >= TODAY  # inside the window, so the flag is what blocks it


def test_12_consumable_personalized_and_gift_card_items(order_for: Fetch) -> None:
    order = order_for(12)
    assert flags(order) == {"consumable", "personalized", "gift_card"}
    assert {tuple(i["item_flags"]) for i in order["items"]} == {
        ("consumable",),
        ("personalized",),
        ("gift_card",),
    }


def test_13_partial_refund_multi_item(order_for: Fetch) -> None:
    order = order_for(13)
    assert len(order["items"]) == 2
    assert order["refund_record"] is None
    assert order["order_value_paise"] == sum(
        i["unit_price_paise"] * i["quantity"] - i["discount_applied_paise"] for i in order["items"]
    )


def test_14_refund_already_issued(order_for: Fetch) -> None:
    order = order_for(14)
    refund = order["refund_record"]
    assert refund["amount_paise"] == order["order_value_paise"]
    assert refund["issued_at"].endswith("Z")
    assert parse(refund["issued_at"]) < NOW


def test_15_cod_within_limit(order_for: Fetch) -> None:
    order = order_for(15)
    assert order["payment_method"] == "cod"
    assert order["order_value_paise"] <= constant("cod_limit_paise")


def test_16_high_value_prepaid(order_for: Fetch) -> None:
    order = order_for(16)
    assert order["payment_method"] != "cod"
    assert order["order_value_paise"] > constant("high_value_threshold_paise")


def test_17_wrong_address_after_dispatch(order_for: Fetch) -> None:
    order = order_for(17)
    assert order["status"] == "dispatched"
    assert order["courier_tracking_number"]


def test_18_unknown_order_id_is_404(client: TestClient, scenarios: Scenarios) -> None:
    scenario = scenarios[18]
    response = client.get(f"/orders/{scenario.order_id}", params={"email": scenario.customer_email})
    assert response.status_code == 404


def test_19_other_customers_order_is_403_without_data(
    client: TestClient, scenarios: Scenarios
) -> None:
    scenario = scenarios[19]
    response = client.get(f"/orders/{scenario.order_id}", params={"email": scenario.customer_email})
    assert response.status_code == 403
    assert response.json() == {"detail": "order does not belong to this customer"}


def test_20_customer_with_several_orders(client: TestClient, scenarios: Scenarios) -> None:
    scenario = scenarios[20]
    assert scenario.order_id is None
    history = client.get(f"/customers/{scenario.customer_email}/history").json()
    assert history["total_orders"] == 3
    last = client.get(
        f"/orders/{history['last_order_id']}", params={"email": scenario.customer_email}
    )
    assert last.status_code == 200


def test_21_repeat_customer_with_prior_complaints(client: TestClient, scenarios: Scenarios) -> None:
    history = client.get(f"/customers/{scenarios[21].customer_email}/history").json()
    assert history["prior_complaint_count"] >= 3


def test_22_duplicate_submission_has_an_order(order_for: Fetch) -> None:
    assert order_for(22)["status"] == "dispatched"


def test_23_reply_on_existing_thread_has_an_order(order_for: Fetch) -> None:
    assert order_for(23)["status"] == "dispatched"


def test_24_failed_delivery_after_three_attempts(order_for: Fetch) -> None:
    order = order_for(24)
    assert order["status"] == "failed_delivery"
    assert order["failed_delivery_attempts"] == 3
    statuses = [e["status"] for e in order["courier_events"]]
    assert statuses.count("delivery_attempted") == 3
    assert statuses[-1] == "returned_to_warehouse"


def test_25_store_credit_used(order_for: Fetch) -> None:
    order = order_for(25)
    item = order["items"][0]
    assert item["store_credit_used_paise"] > 0
    assert item["discount_applied_paise"] > 0
    assert order["refund_record"] is None


def test_26_remote_pin_code(order_for: Fetch) -> None:
    order = order_for(26)
    assert order["shipping_tier"] == "remote"
    assert order["status"] == "dispatched"


def test_27_sunday_order_dispatched_next_business_day(order_for: Fetch) -> None:
    order = order_for(27)
    placed, dispatched = ist_date(order["placed_at"]), ist_date(order["dispatched_at"])
    assert placed.weekday() == 6  # Sunday
    assert dispatched == placed + timedelta(days=1)  # Monday


def test_28_damage_reported_without_photo_awaiting_followup(order_for: Fetch) -> None:
    order = order_for(28)
    assert order["damage_report"]["has_photo"] is False
    assert hours_between(order["damage_report"]["reported_at"], order["delivered_at"]) <= constant(
        "damage_window_hours"
    )


def test_29_non_english_message_has_an_order(order_for: Fetch) -> None:
    assert order_for(29)["status"] == "delivered"


def test_30_multi_intent_message_has_an_order(order_for: Fetch) -> None:
    order = order_for(30)
    assert order["status"] == "dispatched"


def test_31_warranty_claim_inside_six_months(order_for: Fetch) -> None:
    order = order_for(31)
    assert flags(order) == {"small_appliance"}
    months_ago_limit = constant("warranty_months") * 31
    assert (NOW - parse(order["delivered_at"])).days < months_ago_limit
