"""Post-lookup routing on the seeded mock-store orders, at the frozen time the seed is built for."""

from datetime import timedelta
from typing import Any

import pytest

from services.api.context.lookup import ContextLookup, LookupResult
from services.api.escalation.post_lookup import Route, decide
from services.api.models import Order
from services.api.models.enums import Flag, Intent, Sentiment, Urgency
from services.api.models.ticket import Classification
from tests.context.support import NOW, seeded

ORDERS = {order.order_id: order for order in seeded().orders}
CASE_ORDER = {s.case: ORDERS[s.order_id] for s in seeded().scenarios if s.order_id in ORDERS}


def classification(intent: Intent = Intent.ORDER_STATUS, *flags: Flag) -> Classification:
    return Classification(
        intent=intent,
        urgency=Urgency.NORMAL,
        confidence=0.9,
        sentiment=Sentiment.NEUTRAL,
        flags=list(flags),
    )


def found(order: Order) -> ContextLookup:
    return ContextLookup(LookupResult.FOUND, order.order_id, 1, order)


def route(
    intent: Intent,
    lookup: ContextLookup,
    *flags: Flag,
    messages: int = 1,
    at: Any = NOW,
) -> Any:
    return decide(classification(intent, *flags), lookup, thread_messages=messages, received_at=at)


# --- no draft ------------------------------------------------------------------------------


def test_an_order_that_does_not_exist_goes_to_the_owner() -> None:
    decision = route(Intent.ORDER_STATUS, ContextLookup(LookupResult.NOT_FOUND, "RD-99999", 1))
    assert (decision.route, decision.reasons) == (Route.NO_DRAFT, ["order_not_found"])


def test_another_customers_order_goes_to_the_owner_and_is_flagged() -> None:
    decision = route(Intent.ORDER_STATUS, ContextLookup(LookupResult.MISMATCH, "RD-10018", 1))
    assert (decision.route, decision.reasons) == (Route.NO_DRAFT, ["order_mismatch"])
    assert Flag.ORDER_MISMATCH in decision.flags


def test_an_order_above_ten_thousand_rupees_goes_to_the_owner_with_the_flag() -> None:
    decision = route(Intent.ORDER_STATUS, found(CASE_ORDER[16]))
    assert (decision.route, decision.reasons) == (Route.NO_DRAFT, ["high_value"])
    assert Flag.HIGH_VALUE in decision.flags


def test_the_value_limit_is_strictly_above() -> None:
    at_limit = CASE_ORDER[16].model_copy(update={"order_value_paise": 1_000_000})
    assert route(Intent.ORDER_STATUS, found(at_limit)).route is Route.DRAFT
    over = at_limit.model_copy(update={"order_value_paise": 1_000_001})
    assert route(Intent.ORDER_STATUS, found(over)).route is Route.NO_DRAFT


@pytest.mark.parametrize(("messages", "escalates"), [(1, False), (2, False), (3, True), (4, True)])
def test_the_third_message_on_a_thread_is_a_repeat_complaint(
    messages: int, escalates: bool
) -> None:
    decision = route(Intent.ORDER_STATUS, found(CASE_ORDER[1]), messages=messages)
    assert (decision.route is Route.NO_DRAFT) is escalates
    assert (Flag.REPEAT_COMPLAINT in decision.flags) is escalates


def test_every_no_draft_reason_that_applies_is_listed() -> None:
    decision = route(Intent.ORDER_STATUS, found(CASE_ORDER[16]), messages=3)
    assert decision.reasons == ["repeat_complaint", "high_value"]
    assert decision.reason == "repeat_complaint,high_value"


def test_the_classifier_alone_cannot_make_a_repeat_complaint_or_high_value() -> None:
    decision = route(Intent.ORDER_STATUS, found(CASE_ORDER[1]), Flag.REPEAT_COMPLAINT)
    assert decision.route is Route.DRAFT


def test_no_draft_beats_outside_policy() -> None:
    lookup = found(CASE_ORDER[16])
    decision = route(Intent.RETURN, lookup, Flag.COMPENSATION_REQUEST)
    assert decision.route is Route.NO_DRAFT and decision.triggers == []


# --- suggestion only -----------------------------------------------------------------------


def test_a_return_after_the_window_is_suggestion_only() -> None:
    decision = route(Intent.RETURN, found(CASE_ORDER[2]))  # delivered 7 Oct, asked 15 Oct
    assert decision.route is Route.SUGGESTION_ONLY
    assert (decision.reasons, decision.triggers) == (["outside_policy"], ["return_window_closed"])


def test_a_return_inside_the_window_is_drafted() -> None:
    assert route(Intent.RETURN, found(CASE_ORDER[1])).route is Route.DRAFT


def test_the_return_window_is_judged_at_the_time_the_customer_wrote() -> None:
    order = CASE_ORDER[1]  # delivered 12 Oct, 11:30 IST
    last_second = order.delivered_at + timedelta(days=7, hours=12, minutes=29, seconds=59)
    assert route(Intent.RETURN, found(order), at=last_second).route is Route.DRAFT
    assert route(Intent.RETURN, found(order), at=last_second + timedelta(seconds=1)).route is (
        Route.SUGGESTION_ONLY
    )


def test_a_refund_request_on_a_delivered_order_follows_the_return_window() -> None:
    assert route(Intent.REFUND, found(CASE_ORDER[2])).route is Route.SUGGESTION_ONLY


def test_a_refund_already_issued_is_not_a_late_return() -> None:
    assert route(Intent.REFUND, found(CASE_ORDER[14])).route is Route.DRAFT


def test_a_damage_report_after_forty_eight_hours_is_suggestion_only() -> None:
    order = CASE_ORDER[10]  # delivered 10 Oct
    decision = route(Intent.DAMAGED_ITEM, found(order))
    assert decision.route is Route.SUGGESTION_ONLY
    assert decision.triggers == ["damage_window_closed"]


def test_a_damage_report_inside_the_window_is_drafted() -> None:
    order = CASE_ORDER[9]  # delivered 14 Oct 11:30 IST, reported the next day
    assert route(Intent.DAMAGED_ITEM, found(order)).route is Route.DRAFT


def test_the_damage_window_edge_is_forty_eight_hours_exactly() -> None:
    order = CASE_ORDER[9]
    edge = order.delivered_at + timedelta(hours=48)
    assert route(Intent.DAMAGED_ITEM, found(order), at=edge).route is Route.DRAFT
    over = edge + timedelta(seconds=1)
    assert route(Intent.DAMAGED_ITEM, found(order), at=over).route is Route.SUGGESTION_ONLY


def test_a_compensation_request_is_suggestion_only_even_without_an_order() -> None:
    lookup = ContextLookup(LookupResult.NO_ORDER)
    decision = route(Intent.ORDER_STATUS, lookup, Flag.COMPENSATION_REQUEST)
    assert (decision.route, decision.triggers) == (Route.SUGGESTION_ONLY, ["compensation_request"])


def test_a_refund_for_a_final_sale_item_that_is_not_damaged_is_suggestion_only() -> None:
    decision = route(Intent.REFUND, found(CASE_ORDER[11]))
    assert decision.route is Route.SUGGESTION_ONLY
    assert decision.triggers == ["final_sale_not_damaged"]


def test_a_final_sale_item_that_is_damaged_is_not_held_back_by_that_rule() -> None:
    order = CASE_ORDER[11]
    soon = order.delivered_at + timedelta(hours=1)
    assert route(Intent.DAMAGED_ITEM, found(order), at=soon).route is Route.DRAFT
    reported = CASE_ORDER[10].damage_report
    with_report = order.model_copy(update={"damage_report": reported})
    assert route(Intent.REFUND, found(with_report)).route is Route.DRAFT


def test_a_late_return_of_a_final_sale_item_lists_both_triggers() -> None:
    order = CASE_ORDER[11].model_copy(update={"delivered_at": NOW - timedelta(days=30)})
    assert route(Intent.RETURN, found(order)).triggers == [
        "return_window_closed",
        "final_sale_not_damaged",
    ]


def test_an_order_not_yet_delivered_has_no_window_to_miss() -> None:
    assert route(Intent.RETURN, found(CASE_ORDER[3])).route is Route.DRAFT


# --- draft ---------------------------------------------------------------------------------


@pytest.mark.parametrize("result", [LookupResult.AMBIGUOUS, LookupResult.NO_ORDER])
def test_an_order_that_could_not_be_pinned_down_is_drafted_to_ask_which(
    result: LookupResult,
) -> None:
    decision = route(Intent.ORDER_STATUS, ContextLookup(result))
    assert (decision.route, decision.reasons) == (Route.DRAFT, [])


def test_an_ordinary_ticket_keeps_the_classifiers_flags() -> None:
    decision = route(Intent.ORDER_STATUS, found(CASE_ORDER[1]), Flag.ANGRY)
    assert decision.route is Route.DRAFT and decision.flags == [Flag.ANGRY]
