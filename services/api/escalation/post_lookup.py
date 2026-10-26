"""Post-lookup escalation (spec section 14.2): what the owner sees once the order is known.

Three routes, decided by plain code with no model call:

- **No draft** (`order_mismatch`, `order_not_found`, `repeat_complaint`, `high_value`): the owner
  handles the ticket. Every reason that applies is recorded. `high_value` (ESC-8.1) and
  `repeat_complaint` (ESC-8.2) are set here, from the order and the thread, never by the model.
- **Suggestion only** (`outside_policy`, ESC-8.3): a draft is made for the owner but cannot be
  one-click approved. Triggers: a return after the window (RET-1.5), a damage report after 48
  hours (DMG-3.3), a compensation request, a refund or return of a final-sale item that is not
  damaged. What no rule here can see (a product question the FAQ cannot answer) is decided where
  the FAQ is searched.
- **Draft**: everything else. An order that could not be pinned down (`ambiguous`, `no_order`) is
  not an escalation: the draft asks the customer which order they mean.

No-draft reasons win over outside-policy ones: a ticket the owner must handle gets no draft at all.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from services.api.context.lookup import ContextLookup, LookupResult
from services.api.models import Order
from services.api.models.enums import Flag, Intent, ItemFlag
from services.api.models.ticket import Classification
from services.api.policy_constants import constant
from services.api.time_utils import damage_window_open, return_window_open


class Route(StrEnum):
    DRAFT = "draft"
    NO_DRAFT = "no_draft"
    SUGGESTION_ONLY = "suggestion_only"


@dataclass(frozen=True)
class PostLookupDecision:
    route: Route
    reasons: list[str] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)  # the ticket's flags after this step
    triggers: list[str] = field(default_factory=list)  # which outside-policy rules matched

    @property
    def reason(self) -> str:
        return ",".join(self.reasons)


_RETURN_INTENTS = (Intent.RETURN, Intent.REFUND)


def _outside_policy_triggers(
    classification: Classification, order: Order | None, received_at: datetime
) -> list[str]:
    triggers = []
    if Flag.COMPENSATION_REQUEST in classification.flags:
        triggers.append("compensation_request")
    if order is None:
        return triggers

    delivered_at = order.delivered_at
    if delivered_at is not None:
        if (
            classification.intent in _RETURN_INTENTS
            and order.refund_record is None  # already refunded: REF-2.6 covers it
            and not return_window_open(delivered_at, received_at)
        ):
            triggers.append("return_window_closed")
        if classification.intent is Intent.DAMAGED_ITEM and not damage_window_open(
            delivered_at, received_at
        ):
            triggers.append("damage_window_closed")

    if (
        classification.intent in _RETURN_INTENTS
        and order.damage_report is None
        and any(ItemFlag.FINAL_SALE in item.item_flags for item in order.items)
    ):
        triggers.append("final_sale_not_damaged")
    return triggers


def decide(
    classification: Classification,
    lookup: ContextLookup,
    *,
    thread_messages: int,
    received_at: datetime,
) -> PostLookupDecision:
    """`thread_messages` counts the ticket's own message and every earlier one on its thread."""
    flags = list(classification.flags)
    reasons: list[str] = []

    if lookup.result is LookupResult.MISMATCH:
        reasons.append("order_mismatch")
        if Flag.ORDER_MISMATCH not in flags:
            flags.append(Flag.ORDER_MISMATCH)
    elif lookup.result is LookupResult.NOT_FOUND:
        reasons.append("order_not_found")

    if thread_messages >= constant("repeat_complaint_message_count"):
        reasons.append("repeat_complaint")
        if Flag.REPEAT_COMPLAINT not in flags:
            flags.append(Flag.REPEAT_COMPLAINT)

    order = lookup.order
    if order is not None and order.order_value_paise > constant("high_value_threshold_paise"):
        reasons.append("high_value")
        if Flag.HIGH_VALUE not in flags:
            flags.append(Flag.HIGH_VALUE)

    if reasons:
        return PostLookupDecision(Route.NO_DRAFT, reasons, flags)

    triggers = _outside_policy_triggers(classification, order, received_at)
    if triggers:
        return PostLookupDecision(Route.SUGGESTION_ONLY, ["outside_policy"], flags, triggers)
    return PostLookupDecision(Route.DRAFT, [], flags)
