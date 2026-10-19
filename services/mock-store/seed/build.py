"""Builds the seeded orders, customers and the scenario index."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import Field

from services.api.models import CustomerHistory, Order
from services.api.models.common import StrictModel, UtcDatetime
from services.api.policy_constants import constant

IST = ZoneInfo("Asia/Kolkata")
UTC_ZONE = ZoneInfo("UTC")


class Scenario(StrictModel):
    """One required edge case. `order_id` is None where the case has no single order."""

    case: int = Field(ge=1, le=31)
    title: str
    policies: list[str]
    customer_email: str
    order_id: str | None
    note: str = ""


class SeedData(StrictModel):
    now: UtcDatetime
    scenarios: list[Scenario]
    orders: list[Order]
    histories: list[CustomerHistory]


def _ist_date(moment: datetime) -> date:
    return moment.astimezone(IST).date()


class _Builder:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.today = _ist_date(now)
        self.orders: list[dict[str, object]] = []
        self.scenarios: list[Scenario] = []
        self.complaints: dict[str, int] = {}
        self._next_id = 10001

    # --- time helpers ---------------------------------------------------------------------
    def ago(self, *, days: int = 0, hours: int = 0) -> datetime:
        return self.now - timedelta(days=days, hours=hours)

    def ist_at(self, days_ago: int, hour: int, minute: int = 0) -> datetime:
        """Wall-clock time in IST on the IST date `days_ago` days before today, in UTC."""
        day = self.today - timedelta(days=days_ago)
        local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
        return local.astimezone(UTC_ZONE)

    def last_sunday_before(self, days_ago: int) -> int:
        """Smallest `days_ago` value, at least the one given, that falls on a Sunday in IST."""
        offset = days_ago
        while (self.today - timedelta(days=offset)).weekday() != 6:
            offset += 1
        return offset

    # --- building blocks ------------------------------------------------------------------
    @staticmethod
    def item(
        item_id: str,
        name: str,
        price_rupees: int,
        *,
        quantity: int = 1,
        flags: tuple[str, ...] = (),
        discount_rupees: int = 0,
        credit_rupees: int = 0,
    ) -> dict[str, object]:
        return {
            "item_id": item_id,
            "product_name": name,
            "quantity": quantity,
            "unit_price_paise": price_rupees * 100,
            "item_flags": list(flags),
            "discount_applied_paise": discount_rupees * 100,
            "store_credit_used_paise": credit_rupees * 100,
        }

    @staticmethod
    def trail(
        dispatched_at: datetime, *, delivered_at: datetime | None = None
    ) -> list[dict[str, object]]:
        events: list[dict[str, object]] = [
            _event(dispatched_at + timedelta(hours=3), "picked_up", "Warehouse", None),
            _event(dispatched_at + timedelta(days=1), "in_transit", "Regional hub", None),
        ]
        if delivered_at is not None:
            events.append(
                _event(delivered_at - timedelta(hours=3), "out_for_delivery", "Local depot", None)
            )
            events.append(_event(delivered_at, "delivered", None, "Handed to customer"))
        return events

    def add_order(
        self,
        email: str,
        items: list[dict[str, object]],
        *,
        status: str,
        placed_at: datetime,
        dispatched_at: datetime | None = None,
        delivered_at: datetime | None = None,
        payment: str = "upi",
        tier: str = "standard",
        cancellation: dict[str, object] | None = None,
        refund: dict[str, object] | None = None,
        damage: dict[str, object] | None = None,
        eta: datetime | None = None,
        attempts: int = 0,
        events: list[dict[str, object]] | None = None,
    ) -> str:
        order_id = f"RD-{self._next_id}"
        self._next_id += 1
        value = sum(
            int(i["unit_price_paise"]) * int(i["quantity"]) - int(i["discount_applied_paise"])  # type: ignore[call-overload]
            for i in items
        )
        shipping = (
            0
            if value >= constant("free_shipping_threshold_paise")
            else constant("shipping_fee_paise")
        )
        if events is None and dispatched_at is not None:
            events = self.trail(dispatched_at, delivered_at=delivered_at)
        self.orders.append(
            {
                "order_id": order_id,
                "customer_email": email,
                "status": status,
                "placed_at": placed_at,
                "dispatched_at": dispatched_at,
                "delivered_at": delivered_at,
                "payment_method": payment,
                "order_value_paise": value,
                "shipping_charge_paise": shipping,
                "shipping_tier": tier,
                "items": items,
                "cancellation_record": cancellation,
                "refund_record": refund,
                "damage_report": damage,
                "courier_tracking_number": (
                    f"TRK{order_id[3:]}0000" if dispatched_at is not None else None
                ),
                "courier_eta": eta,
                "failed_delivery_attempts": attempts,
                "courier_events": events or [],
            }
        )
        return order_id

    def case(
        self,
        number: int,
        title: str,
        policies: list[str],
        email: str,
        order_id: str | None,
        note: str = "",
    ) -> None:
        self.scenarios.append(
            Scenario(
                case=number,
                title=title,
                policies=policies,
                customer_email=email,
                order_id=order_id,
                note=note,
            )
        )


def _event(at: datetime, status: str, location: str | None, note: str | None) -> dict[str, object]:
    return {"occurred_at": at, "status": status, "location": location, "note": note}


def _delivered(
    b: _Builder, email: str, items: list[dict[str, object]], days_ago: int, **extra: object
) -> str:
    """A delivered, prepaid order whose delivery was `days_ago` IST calendar days ago at 11:30."""
    delivered_at = b.ist_at(days_ago, 11, 30)
    dispatched_at = delivered_at - timedelta(days=4)
    return b.add_order(
        email,
        items,
        status="delivered",
        placed_at=dispatched_at - timedelta(days=1),
        dispatched_at=dispatched_at,
        delivered_at=delivered_at,
        **extra,  # type: ignore[arg-type]
    )


def build_seed(now: datetime) -> SeedData:
    b = _Builder(now)
    damage_hours = constant("damage_window_hours")
    return_days = constant("return_window_days")
    warranty_days = constant("warranty_months") * 30

    # 1. Delivered on time, inside the return window.
    email = "asha.rao@example.com"
    oid = _delivered(b, email, [b.item("1", "Stainless steel water bottle", 649)], 3)
    b.case(1, "Delivered, inside the 7-day return window", ["RET-1.1"], email, oid)

    # 2. Return window expired by exactly one IST calendar day.
    email = "vikram.shah@example.com"
    oid = _delivered(b, email, [b.item("1", "Cotton bedsheet set", 1299)], return_days + 1)
    b.case(2, "Return window expired by exactly 1 day", ["RET-1.5"], email, oid)

    # 3. In transit, inside the estimated window.
    email = "neha.kulkarni@example.com"
    dispatched = b.ist_at(2, 10)
    oid = b.add_order(
        email,
        [b.item("1", "Ceramic dinner set", 1899)],
        status="dispatched",
        placed_at=dispatched - timedelta(days=1),
        dispatched_at=dispatched,
    )
    b.case(3, "In transit, within the estimated window", ["SHP-4.2"], email, oid)

    # 4. Late beyond the estimate, no courier ETA.
    email = "rohit.menon@example.com"
    dispatched = b.ist_at(14, 10)
    oid = b.add_order(
        email,
        [b.item("1", "Non-stick kadhai", 899)],
        status="dispatched",
        placed_at=dispatched - timedelta(days=1),
        dispatched_at=dispatched,
    )
    b.case(4, "Late, no courier ETA", ["SHP-4.5"], email, oid)

    # 5. Late, courier has given a new ETA.
    email = "priya.nair@example.com"
    dispatched = b.ist_at(13, 10)
    oid = b.add_order(
        email,
        [b.item("1", "Pressure cooker 5L", 1599)],
        status="dispatched",
        placed_at=dispatched - timedelta(days=1),
        dispatched_at=dispatched,
        eta=b.now + timedelta(days=2),
    )
    b.case(5, "Late, courier has a new ETA", ["SHP-4.5"], email, oid)

    # 6. Placed, not yet dispatched (cancellable).
    email = "arjun.desai@example.com"
    oid = b.add_order(
        email,
        [b.item("1", "Bamboo cutting board", 549)],
        status="placed",
        placed_at=b.ago(hours=20),
    )
    b.case(6, "Not yet shipped, cancellable", ["CAN-5.1"], email, oid)

    # 7. Already cancelled.
    email = "sneha.iyer@example.com"
    placed = b.ago(days=4)
    oid = b.add_order(
        email,
        [b.item("1", "Glass storage jars (set of 3)", 799)],
        status="cancelled",
        placed_at=placed,
        cancellation={
            "cancelled_at": b.ago(days=3),
            "cancelled_by": "customer",
            "reason": "Ordered by mistake",
        },
    )
    b.case(7, "Already cancelled", ["CAN-5.1"], email, oid)

    # 8. Damage report inside the window, with a photo.
    email = "kabir.malhotra@example.com"
    delivered = b.ago(hours=20)
    oid = b.add_order(
        email,
        [b.item("1", "Glass water jug", 749)],
        status="delivered",
        placed_at=delivered - timedelta(days=5),
        dispatched_at=delivered - timedelta(days=3),
        delivered_at=delivered,
        damage={
            "reported_at": delivered + timedelta(hours=18),
            "has_photo": True,
            "description": "The jug arrived cracked along the base.",
        },
    )
    b.case(8, "Damage report inside 48 hours, with photo", ["DMG-3.1", "DMG-3.2"], email, oid)

    # 9. Damage report without a photo.
    email = "divya.pillai@example.com"
    delivered = b.ago(hours=30)
    oid = b.add_order(
        email,
        [b.item("1", "Ceramic planter", 599)],
        status="delivered",
        placed_at=delivered - timedelta(days=5),
        dispatched_at=delivered - timedelta(days=3),
        delivered_at=delivered,
        damage={
            "reported_at": delivered + timedelta(hours=27),
            "has_photo": False,
            "description": "The planter was broken when it arrived.",
        },
    )
    b.case(9, "Damage report with no photo", ["DMG-3.4"], email, oid)

    # 10. Damage report after the 48-hour limit.
    email = "manish.joshi@example.com"
    delivered = b.ago(days=5)
    oid = b.add_order(
        email,
        [b.item("1", "Steel tiffin box", 449)],
        status="delivered",
        placed_at=delivered - timedelta(days=5),
        dispatched_at=delivered - timedelta(days=3),
        delivered_at=delivered,
        damage={
            "reported_at": delivered + timedelta(hours=damage_hours + 48),
            "has_photo": True,
            "description": "The lid is bent and does not close.",
        },
    )
    b.case(10, "Damage report after the 48-hour limit", ["DMG-3.3"], email, oid)

    # 11. Final-sale item, return requested without damage.
    email = "anita.gupta@example.com"
    oid = _delivered(b, email, [b.item("1", "Clearance table lamp", 799, flags=("final_sale",))], 2)
    b.case(11, "Final-sale item return request", ["RET-1.4", "ESC-8.3"], email, oid)

    # 12. Consumable, personalized and gift-card items on one order.
    email = "farhan.sheikh@example.com"
    oid = _delivered(
        b,
        email,
        [
            b.item("1", "Spice blend gift tin", 349, flags=("consumable",)),
            b.item("2", "Engraved cutting board", 899, flags=("personalized",)),
            b.item("3", "Store gift card", 500, flags=("gift_card",)),
        ],
        2,
    )
    b.case(12, "Consumable, personalized and gift-card returns", ["RET-1.4"], email, oid)

    # 13. Partial refund: one item of a multi-item order.
    email = "lakshmi.reddy@example.com"
    oid = _delivered(
        b,
        email,
        [
            b.item("1", "Cotton kitchen towels (pack of 4)", 399),
            b.item("2", "Silicone spatula set", 599),
        ],
        3,
    )
    b.case(13, "Partial refund, one item of two", ["REF-2.4"], email, oid)

    # 14. Refund already issued.
    email = "sandeep.verma@example.com"
    delivered = b.ist_at(6, 11, 30)
    oid = b.add_order(
        email,
        [b.item("1", "Electric kettle", 1199)],
        status="delivered",
        placed_at=delivered - timedelta(days=5),
        dispatched_at=delivered - timedelta(days=4),
        delivered_at=delivered,
        refund={
            "refund_id": "RF-5001",
            "issued_at": b.ago(days=2),
            "amount_paise": 119900,
            "method": "original_payment",
        },
    )
    b.case(14, "Refund already issued", ["REF-2.6"], email, oid)

    # 15. COD order, refund request, at most the COD limit.
    email = "pooja.bhatt@example.com"
    oid = _delivered(b, email, [b.item("1", "Iron tawa set", 2499)], 4, payment="cod")
    b.case(15, "COD order refund request", ["REF-2.3", "PAY-7.1"], email, oid)

    # 16. High-value prepaid order.
    email = "rajesh.khanna@example.com"
    oid = _delivered(b, email, [b.item("1", "Cast iron cookware collection", 12499)], 3)
    b.case(16, "High-value prepaid order", ["ESC-8.1"], email, oid)

    # 17. Wrong address entered by the customer; the order has already been dispatched.
    email = "tanvi.chopra@example.com"
    dispatched = b.ist_at(1, 10)
    oid = b.add_order(
        email,
        [b.item("1", "Wall clock", 699)],
        status="dispatched",
        placed_at=dispatched - timedelta(days=1),
        dispatched_at=dispatched,
        tier="metro",
    )
    b.case(
        17,
        "Wrong address (customer error), already dispatched",
        ["SHP-4.6"],
        email,
        oid,
        "The contract carries no address, so the address is part of the customer's message.",
    )

    # 18. The order id does not exist.
    email = "ghost.customer@example.com"
    b.case(
        18,
        "Order ID does not exist",
        ["FR-5"],
        email,
        "RD-99999",
        "Deliberately not seeded: GET /orders/RD-99999 returns 404.",
    )

    # 19. Order belongs to a different customer (privacy test).
    victim = "meenal.kapoor@example.com"
    oid = _delivered(b, victim, [b.item("1", "Linen cushion covers", 899)], 3)
    b.case(
        19,
        "Order belongs to a different customer",
        ["TON-9.4", "ESC-8.2"],
        "someone.else@example.com",
        oid,
        "customer_email is the asker; the order is the victim's. The store returns 403, no data.",
    )

    # 20. Customer with several orders and an ambiguous message.
    email = "gaurav.mehta@example.com"
    _delivered(b, email, [b.item("1", "Bath towels (pair)", 799)], 20)
    _delivered(b, email, [b.item("1", "Dish rack", 649)], 9)
    last = b.add_order(
        email,
        [b.item("1", "Floor mat", 499)],
        status="dispatched",
        placed_at=b.ist_at(3, 10),
        dispatched_at=b.ist_at(2, 10),
    )
    b.case(
        20,
        "Several orders, ambiguous message",
        ["FR-5"],
        email,
        None,
        f"Three orders; the newest is {last}. The message names none of them.",
    )

    # 21. Repeat customer with prior complaints.
    email = "shalini.dutta@example.com"
    b.complaints[email] = 4
    oid = b.add_order(
        email,
        [b.item("1", "Induction cooktop", 2299, flags=("small_appliance",))],
        status="dispatched",
        placed_at=b.ist_at(9, 10),
        dispatched_at=b.ist_at(8, 10),
    )
    b.case(21, "Repeat customer with prior complaints", ["ESC-8.2"], email, oid)

    # 22. Duplicate message submission.
    email = "imran.qureshi@example.com"
    dispatched = b.ist_at(2, 10)
    oid = b.add_order(
        email,
        [b.item("1", "Hand blender", 1099)],
        status="dispatched",
        placed_at=dispatched - timedelta(days=1),
        dispatched_at=dispatched,
    )
    b.case(22, "Duplicate message submission", ["FR-2"], email, oid, "Same Message-ID sent twice.")

    # 23. Customer replying on an existing thread.
    email = "kavita.singh@example.com"
    dispatched = b.ist_at(3, 10)
    oid = b.add_order(
        email,
        [b.item("1", "Glass baking dish", 749)],
        status="dispatched",
        placed_at=dispatched - timedelta(days=1),
        dispatched_at=dispatched,
    )
    b.case(23, "Reply on an existing thread", ["FR-2"], email, oid, "Message has In-Reply-To set.")

    # 24. Failed delivery after three attempts, returned to warehouse.
    email = "naveen.kumar@example.com"
    dispatched = b.ist_at(9, 10)
    oid = b.add_order(
        email,
        [b.item("1", "Wooden spice rack", 999)],
        status="failed_delivery",
        placed_at=dispatched - timedelta(days=1),
        dispatched_at=dispatched,
        attempts=3,
        events=[
            _event(dispatched + timedelta(hours=3), "picked_up", "Warehouse", None),
            _event(
                dispatched + timedelta(days=3), "delivery_attempted", "Local depot", "No one home"
            ),
            _event(
                dispatched + timedelta(days=4), "delivery_attempted", "Local depot", "No one home"
            ),
            _event(
                dispatched + timedelta(days=5),
                "delivery_attempted",
                "Local depot",
                "Address locked",
            ),
            _event(dispatched + timedelta(days=6), "returned_to_warehouse", "Warehouse", None),
        ],
    )
    b.case(24, "Failed delivery after 3 attempts", ["SHP-4.7"], email, oid)

    # 25. Order paid partly with store credit; refund to store credit.
    email = "ritu.agarwal@example.com"
    oid = _delivered(
        b,
        email,
        [b.item("1", "Bamboo steamer set", 1299, discount_rupees=100, credit_rupees=500)],
        4,
    )
    b.case(25, "Store credit used, refund to store credit", ["REF-2.5"], email, oid)

    # 26. Remote pin code, still inside the longer estimate.
    email = "tenzin.dorje@example.com"
    dispatched = b.ist_at(6, 10)
    oid = b.add_order(
        email,
        [b.item("1", "Woolen throw blanket", 1499)],
        status="dispatched",
        placed_at=dispatched - timedelta(days=1),
        dispatched_at=dispatched,
        tier="remote",
    )
    b.case(26, "Remote pin code", ["SHP-4.2"], email, oid)

    # 27. Ordered on a Sunday, dispatched the next business day.
    email = "alok.pandey@example.com"
    sunday = b.last_sunday_before(2)
    dispatched = b.ist_at(sunday - 1, 11)
    oid = b.add_order(
        email,
        [b.item("1", "Brass diya set", 699)],
        status="dispatched",
        placed_at=b.ist_at(sunday, 15),
        dispatched_at=dispatched,
    )
    b.case(27, "Sunday order, dispatched next business day", ["SHP-4.1"], email, oid)

    # 28. Damage reported without a photo; the photo arrives in a thread reply.
    email = "swati.jain@example.com"
    delivered = b.ago(hours=36)
    oid = b.add_order(
        email,
        [b.item("1", "Ceramic mug set", 549)],
        status="delivered",
        placed_at=delivered - timedelta(days=5),
        dispatched_at=delivered - timedelta(days=3),
        delivered_at=delivered,
        damage={
            "reported_at": delivered + timedelta(hours=30),
            "has_photo": False,
            "description": "Two mugs are chipped. Photo to follow.",
        },
    )
    b.case(
        28,
        "Photo sent in a thread reply",
        ["DMG-3.4"],
        email,
        oid,
        "The photo is an attachment on the follow-up message.",
    )

    # 29. Non-English message.
    email = "harish.patil@example.com"
    oid = _delivered(b, email, [b.item("1", "Copper water jug", 1199)], 2)
    b.case(29, "Non-English message", ["FR-3", "ESC-8.3"], email, oid)

    # 30. Multi-intent message: where is it, and can I return it.
    email = "nisha.thakur@example.com"
    dispatched = b.ist_at(2, 10)
    oid = b.add_order(
        email,
        [b.item("1", "Serving bowls (set of 4)", 999)],
        status="dispatched",
        placed_at=dispatched - timedelta(days=1),
        dispatched_at=dispatched,
    )
    b.case(30, "Multi-intent message", ["FR-3", "A3"], email, oid)

    # 31. Warranty claim on a small appliance, inside the warranty period.
    email = "deepak.sinha@example.com"
    oid = _delivered(
        b,
        email,
        [b.item("1", "Mixer grinder", 3499, flags=("small_appliance",))],
        warranty_days // 2,
    )
    b.case(31, "Warranty claim on a small appliance", ["PRD-6.3"], email, oid)

    orders = [Order.model_validate(raw) for raw in b.orders]
    return SeedData(
        now=now,
        scenarios=sorted(b.scenarios, key=lambda s: s.case),
        orders=orders,
        histories=_histories(orders, b.complaints),
    )


def _histories(orders: list[Order], complaints: dict[str, int]) -> list[CustomerHistory]:
    by_customer: dict[str, list[Order]] = {}
    for order in orders:
        by_customer.setdefault(str(order.customer_email), []).append(order)
    return [
        CustomerHistory(
            customer_email=email,
            total_orders=len(customer_orders),
            prior_complaint_count=complaints.get(email, 0),
            last_order_id=max(customer_orders, key=lambda o: o.placed_at).order_id,
        )
        for email, customer_orders in sorted(by_customer.items())
    ]
