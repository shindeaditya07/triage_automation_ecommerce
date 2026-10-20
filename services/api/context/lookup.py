"""Deciding which order a ticket is about, and fetching it.

1. Order IDs named in the form field, subject or body are the candidates.
2. Exactly one candidate: ask the store for it, with the sender's address.
3. Several candidates: do not guess (`ambiguous`).
4. None named: if the sender has exactly one order on file, that is the order; several orders is
   `ambiguous`; none, or an unknown sender, is `no_order`.

The store answers a mismatch (the order belongs to another address) with no order data, and this
module keeps it that way: nothing about the order is kept, only the fact of the mismatch. What to
do with each result, escalate or draft, is the next step (post-lookup escalation).
"""

from dataclasses import dataclass
from enum import StrEnum

from services.api.context.client import OrderLookup, OrderStoreClient
from services.api.context.extract import order_ids_in
from services.api.models import CustomerHistory, Order


class LookupResult(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"  # the id does not exist
    MISMATCH = "mismatch"  # the id belongs to another customer
    AMBIGUOUS = "ambiguous"  # several candidate orders; none chosen
    NO_ORDER = "no_order"  # no id named and nothing on file for the sender


@dataclass(frozen=True)
class ContextLookup:
    result: LookupResult
    order_id: str | None = None
    candidates: int = 0
    order: Order | None = None  # only when the result is FOUND
    history: CustomerHistory | None = None


async def look_up_context(
    store: OrderStoreClient,
    *,
    email: str,
    subject: str | None,
    body: str,
    order_id_hint: str | None,
) -> ContextLookup:
    history = await store.get_history(email)
    candidates = order_ids_in(order_id_hint, subject, body)

    if len(candidates) > 1:
        return ContextLookup(LookupResult.AMBIGUOUS, None, len(candidates), history=history)

    if candidates:
        order_id = candidates[0]
    elif history is None or history.total_orders == 0 or history.last_order_id is None:
        return ContextLookup(LookupResult.NO_ORDER, history=history)
    elif history.total_orders > 1:
        return ContextLookup(LookupResult.AMBIGUOUS, None, history.total_orders, history=history)
    else:
        order_id = history.last_order_id

    found, order = await store.get_order(order_id, email)
    count = len(candidates)
    if found is OrderLookup.FOUND:
        return ContextLookup(LookupResult.FOUND, order_id, count, order, history)
    if found is OrderLookup.WRONG_CUSTOMER:
        return ContextLookup(LookupResult.MISMATCH, order_id, count, history=history)
    return ContextLookup(LookupResult.NOT_FOUND, order_id, count, history=history)
