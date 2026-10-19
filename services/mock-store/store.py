"""In-memory order store built from the seed. Emails are compared trimmed and lower-cased."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from services.api.models import CustomerHistory, Order

from .seed import SeedData, build_seed


class Lookup(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    WRONG_CUSTOMER = "wrong_customer"


@dataclass
class Store:
    seed: SeedData
    orders: dict[str, Order] = field(init=False)
    histories: dict[str, CustomerHistory] = field(init=False)

    def __post_init__(self) -> None:
        self.orders = {order.order_id: order for order in self.seed.orders}
        self.histories = {str(h.customer_email): h for h in self.seed.histories}

    @classmethod
    def from_clock(cls, now: datetime) -> "Store":
        return cls(build_seed(now))

    def order_for(self, order_id: str, email: str) -> tuple[Lookup, Order | None]:
        """The order only if it belongs to `email`; a mismatch returns no order data (TON-9.4)."""
        order = self.orders.get(order_id)
        if order is None:
            return Lookup.NOT_FOUND, None
        if str(order.customer_email) != email.strip().lower():
            return Lookup.WRONG_CUSTOMER, None
        return Lookup.FOUND, order

    def history_for(self, email: str) -> CustomerHistory | None:
        return self.histories.get(email.strip().lower())
