"""Which order a ticket is about, against the real seeded mock store (spec section 17)."""

import pytest

from services.api.context.lookup import LookupResult, look_up_context
from tests.context.support import seeded, store_client

pytestmark = pytest.mark.anyio

SCENARIOS = {s.case: s for s in seeded().scenarios}
NO_ORDER_CASES = {18, 19, 20}  # not found, belongs to someone else, ambiguous


async def lookup(email: str, body: str, subject: str | None = None, hint: str | None = None):  # type: ignore[no-untyped-def]
    client, http = store_client()
    async with http:
        return await look_up_context(
            client, email=email, subject=subject, body=body, order_id_hint=hint
        )


@pytest.mark.parametrize("case", sorted(set(SCENARIOS) - NO_ORDER_CASES))
async def test_every_ordinary_case_finds_its_order_by_the_id_in_the_message(case: int) -> None:
    scenario = SCENARIOS[case]
    result = await lookup(scenario.customer_email, f"About my order {scenario.order_id}, please.")
    assert result.result is LookupResult.FOUND
    assert result.order is not None and result.order.order_id == scenario.order_id
    assert result.candidates == 1 and result.history is not None


@pytest.mark.parametrize("case", sorted(set(SCENARIOS) - NO_ORDER_CASES))
async def test_a_sender_with_one_order_is_matched_by_email_when_no_id_is_given(case: int) -> None:
    scenario = SCENARIOS[case]
    result = await lookup(scenario.customer_email, "Where is my parcel?")
    assert result.result is LookupResult.FOUND
    assert result.order is not None and result.order.order_id == scenario.order_id
    assert result.candidates == 0


async def test_case_18_an_id_that_does_not_exist_is_not_found() -> None:
    scenario = SCENARIOS[18]
    result = await lookup(scenario.customer_email, f"Order {scenario.order_id} is missing")
    assert result.result is LookupResult.NOT_FOUND
    assert result.order is None and result.order_id == "RD-99999"
    assert result.history is None  # an unknown customer


async def test_case_19_another_customers_order_is_a_mismatch_with_no_order_data() -> None:
    scenario = SCENARIOS[19]
    result = await lookup(scenario.customer_email, f"Please refund order {scenario.order_id}")
    assert result.result is LookupResult.MISMATCH
    assert result.order is None and result.order_id == scenario.order_id


async def test_case_20_several_orders_and_no_id_is_ambiguous() -> None:
    result = await lookup(SCENARIOS[20].customer_email, "Where is my order?")
    assert result.result is LookupResult.AMBIGUOUS
    assert result.order is None and result.candidates == 3
    assert result.history is not None and result.history.total_orders == 3


async def test_case_20_naming_one_of_the_orders_finds_it() -> None:
    result = await lookup(SCENARIOS[20].customer_email, "Where is RD-10021?")
    assert result.result is LookupResult.FOUND


async def test_two_different_ids_in_one_message_are_ambiguous_and_neither_is_fetched() -> None:
    scenario = SCENARIOS[1]
    result = await lookup(
        scenario.customer_email, f"{scenario.order_id} or {SCENARIOS[2].order_id}?"
    )
    assert result.result is LookupResult.AMBIGUOUS and result.candidates == 2
    assert result.order is None


async def test_the_form_hint_is_a_candidate_and_comes_first() -> None:
    scenario = SCENARIOS[1]
    result = await lookup(scenario.customer_email, "Where is it?", hint=scenario.order_id)
    assert result.result is LookupResult.FOUND and result.candidates == 1


async def test_an_id_in_the_subject_counts() -> None:
    scenario = SCENARIOS[1]
    result = await lookup(
        scenario.customer_email, "Where is it?", subject=f"Re: {scenario.order_id}"
    )
    assert result.result is LookupResult.FOUND


async def test_an_unknown_sender_naming_nothing_has_no_order() -> None:
    result = await lookup("nobody@example.com", "Where is my order?")
    assert result.result is LookupResult.NO_ORDER
    assert result.order is None and result.history is None


async def test_the_address_is_matched_whatever_its_case() -> None:
    scenario = SCENARIOS[1]
    result = await lookup(scenario.customer_email.upper(), "Where is my parcel?")
    assert result.result is LookupResult.FOUND
