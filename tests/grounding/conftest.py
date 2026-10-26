from typing import Any

import pytest

from services.api.grounding.validator import GroundingContext
from services.api.models.draft import Citation
from services.api.models.enums import SourceType
from tests.auth.conftest import (  # noqa: F401 - fixtures for the route tests
    clock,
    make_settings,
    owner_hash,
    store,
)

SIGN_OFF = "Test Store Support"
SENDER = "asha@example.com"

ORDER: dict[str, Any] = {
    "order_id": "RD-10001",
    "customer_email": SENDER,
    "status": "delivered",
    "placed_at": "2026-10-07T06:00:00Z",
    "delivered_at": "2026-10-12T06:00:00Z",
    "order_value_paise": 64900,
    "shipping_charge_paise": 6000,
    "items": [
        {"product_name": "Steel water bottle", "unit_price_paise": 64900, "quantity": 1},
    ],
    "refund_record": None,
    "courier_tracking_number": "TRK100010000",
    "courier_events": [{"status": "delivered", "location": "Local depot", "note": "Handed over"}],
}

POLICIES = {
    "RET-1.1": "Items may be returned within 7 calendar days of delivery if unused.",
    "REF-2.1": "Refunds above ₹5,000 need the owner. Refunds are made within 5-7 business days.",
    "SHP-3.1": "Standard shipping takes 3-5 days.",
}


def order_cite(ref: str) -> Citation:
    return Citation(claim=ref, source_type=SourceType.ORDER_FIELD, source_ref=ref)


def policy_cite(ref: str) -> Citation:
    return Citation(claim=ref, source_type=SourceType.POLICY, source_ref=ref)


@pytest.fixture
def ctx() -> GroundingContext:
    return GroundingContext(
        order=ORDER,
        customer_email=SENDER,
        policy_texts=POLICIES,
        sign_off=SIGN_OFF,
        customer_message="Where is my bottle? Order RD-10001.",
    )


def reply(body: str) -> str:
    return f"{body}\n\n{SIGN_OFF}"
