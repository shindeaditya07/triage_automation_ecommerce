from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from services.api.models import CustomerHistory, Order
from services.api.policy_constants import constant, constants

T0 = "2026-10-01T10:00:00Z"
T1 = "2026-10-02T10:00:00Z"
T2 = "2026-10-05T10:00:00Z"

ITEM = {
    "item_id": "1",
    "product_name": "Bottle",
    "quantity": 2,
    "unit_price_paise": 50000,
    "item_flags": [],
    "discount_applied_paise": 10000,
    "store_credit_used_paise": 0,
}

PLACED: dict[str, Any] = {
    "order_id": "RD-1",
    "customer_email": "  Asha@Example.com ",
    "status": "placed",
    "placed_at": T0,
    "dispatched_at": None,
    "delivered_at": None,
    "payment_method": "upi",
    "order_value_paise": 90000,
    "shipping_charge_paise": 6000,
    "shipping_tier": "metro",
    "items": [ITEM],
    "cancellation_record": None,
    "refund_record": None,
    "damage_report": None,
    "courier_tracking_number": None,
    "courier_eta": None,
    "failed_delivery_attempts": 0,
}


def make(**overrides: Any) -> dict[str, Any]:
    return deepcopy(PLACED) | overrides


DELIVERED = make(status="delivered", dispatched_at=T1, delivered_at=T2)
CANCELLATION = {"cancelled_at": T1, "cancelled_by": "customer", "reason": "changed mind"}


def test_valid_order_normalises_email() -> None:
    assert Order(**PLACED).customer_email == "asha@example.com"


def test_line_amount_is_price_times_quantity_less_discount() -> None:
    assert Order(**PLACED).items[0].line_amount_paise == 90000


@pytest.mark.parametrize(
    "overrides",
    [
        {"order_value_paise": 100000},  # not the sum of line amounts (assumption A1)
        {"items": []},
        {"items": [ITEM, ITEM]},  # duplicate item_id
        {"items": [{**ITEM, "quantity": 0}]},
        {"items": [{**ITEM, "discount_applied_paise": 200000}]},  # negative line amount
        {"items": [{**ITEM, "item_flags": ["final_sale", "final_sale"]}]},
        {"items": [{**ITEM, "item_flags": ["fragile"]}]},
        {"status": "shipped"},
        {"payment_method": "cheque"},
        {"placed_at": "2026-10-01T10:00:00"},  # naive
        {"placed_at": "2026-10-01T15:30:00+05:30"},  # not UTC
        {"bogus": 1},
        {"failed_delivery_attempts": 4},
    ],
)
def test_invalid_orders_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Order(**make(**overrides))


def test_nullable_fields_cannot_be_omitted() -> None:
    document = make()
    del document["refund_record"]
    with pytest.raises(ValidationError, match="refund_record"):
        Order(**document)


def test_courier_events_are_optional() -> None:
    assert Order(**PLACED).courier_events == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"dispatched_at": T1},  # placed but dispatched
        {"status": "dispatched"},  # dispatched without a dispatch time
        {"status": "dispatched", "dispatched_at": T1, "delivered_at": T2},
        {"status": "delivered", "dispatched_at": T1},  # no delivery time
        {"status": "delivered", "dispatched_at": T2, "delivered_at": T1},  # before dispatch
        {"status": "failed_delivery", "dispatched_at": T1, "failed_delivery_attempts": 2},
        {"status": "cancelled", "delivered_at": T2, "cancellation_record": CANCELLATION},
        {"status": "cancelled"},  # no cancellation record
        {"cancellation_record": CANCELLATION},  # record on a placed order
    ],
)
def test_status_combinations(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Order(**make(**overrides))


def test_valid_status_combinations() -> None:
    assert Order(**DELIVERED)
    assert Order(**make(status="dispatched", dispatched_at=T1))
    assert Order(**make(status="cancelled", cancellation_record=CANCELLATION))
    assert Order(**make(status="failed_delivery", dispatched_at=T1, failed_delivery_attempts=3))


def test_damage_report_needs_delivery() -> None:
    report = {"reported_at": T2, "has_photo": True, "description": "cracked"}
    with pytest.raises(ValidationError, match="delivered_at"):
        Order(**make(damage_report=report))
    assert Order(**{**DELIVERED, "damage_report": report})


def test_courier_eta_only_while_dispatched() -> None:
    with pytest.raises(ValidationError, match="courier_eta"):
        Order(**{**DELIVERED, "courier_eta": T2})
    assert Order(**make(status="dispatched", dispatched_at=T1, courier_eta=T2))


def test_cod_limit_comes_from_policy_constants() -> None:
    limit = constant("cod_limit_paise")
    item = {**ITEM, "quantity": 1, "unit_price_paise": limit + 100, "discount_applied_paise": 0}
    over = make(payment_method="cod", items=[item], order_value_paise=limit + 100)
    with pytest.raises(ValidationError, match="COD"):
        Order(**over)
    at_limit = {**item, "unit_price_paise": limit}
    assert Order(**make(payment_method="cod", items=[at_limit], order_value_paise=limit))


def test_courier_events_must_be_oldest_first() -> None:
    events = [
        {"occurred_at": T2, "status": "in_transit", "location": None, "note": None},
        {"occurred_at": T1, "status": "picked_up", "location": None, "note": None},
    ]
    with pytest.raises(ValidationError, match="oldest first"):
        Order(**make(status="dispatched", dispatched_at=T1, courier_events=events))


def test_customer_history_last_order_matches_count() -> None:
    assert CustomerHistory(
        customer_email="A@b.com", total_orders=0, prior_complaint_count=0, last_order_id=None
    )
    with pytest.raises(ValidationError):
        CustomerHistory(
            customer_email="a@b.com", total_orders=2, prior_complaint_count=0, last_order_id=None
        )
    with pytest.raises(ValidationError):
        CustomerHistory(
            customer_email="a@b.com", total_orders=0, prior_complaint_count=0, last_order_id="RD-1"
        )


def test_policy_constants_load_and_reject_unknown_names() -> None:
    assert constants()["damage_window_hours"] == 48
    assert constant("free_shipping_threshold_paise") == 99900
    with pytest.raises(KeyError, match="no_such_constant"):
        constant("no_such_constant")
