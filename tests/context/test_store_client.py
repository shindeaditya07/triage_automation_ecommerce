import json
from typing import Any

import httpx
import pytest

from services.api.context.client import (
    OrderLookup,
    StoreContractError,
    StoreUnavailable,
)
from tests.context.support import seeded, store_client

pytestmark = pytest.mark.anyio


def reply(status: int, body: Any = None) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body if body is not None else {})

    return handler


async def test_a_found_order_is_parsed_into_the_contract_model() -> None:
    scenario = next(s for s in seeded().scenarios if s.case == 1)
    client, http = store_client()
    async with http:
        found, order = await client.get_order(scenario.order_id, scenario.customer_email)
    assert found is OrderLookup.FOUND and order is not None
    assert order.order_id == scenario.order_id


async def test_missing_and_foreign_orders_return_no_order_data() -> None:
    client, http = store_client()
    async with http:
        assert await client.get_order("RD-99999", "ghost.customer@example.com") == (
            OrderLookup.NOT_FOUND,
            None,
        )
        assert await client.get_order("RD-10018", "someone.else@example.com") == (
            OrderLookup.WRONG_CUSTOMER,
            None,
        )


async def test_history_is_parsed_and_an_unknown_customer_is_none() -> None:
    client, http = store_client()
    async with http:
        history = await client.get_history("gaurav.mehta@example.com")
        assert history is not None and history.total_orders == 3
        assert await client.get_history("ghost.customer@example.com") is None


async def test_the_request_carries_the_email_and_quotes_the_path() -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(404)

    client, http = store_client(handler)
    async with http:
        await client.get_order("RD-1/../x", "a+b@example.com")
    assert seen[0].raw_path.startswith(b"/orders/RD-1%2F..%2Fx?")
    assert seen[0].params["email"] == "a+b@example.com"


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_a_failing_store_is_unavailable_without_leaking_the_body(status: int) -> None:
    client, http = store_client(reply(status, {"detail": "secret@example.com"}))
    async with http:
        with pytest.raises(StoreUnavailable) as caught:
            await client.get_order("RD-10001", "secret@example.com")
    assert "secret" not in str(caught.value)


async def test_timeouts_and_connection_errors_are_unavailable() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    for handler in (timeout, refused):
        client, http = store_client(handler)
        async with http:
            with pytest.raises(StoreUnavailable):
                await client.get_history("a@example.com")


@pytest.mark.parametrize("status", [400, 401, 422])
async def test_an_unexpected_client_error_is_a_contract_error(status: int) -> None:
    client, http = store_client(reply(status))
    async with http:
        with pytest.raises(StoreContractError):
            await client.get_order("RD-10001", "a@example.com")


async def test_a_reply_outside_the_contract_is_rejected() -> None:
    client, http = store_client(reply(200, {"order_id": "RD-1"}))
    async with http:
        with pytest.raises(StoreContractError) as caught:
            await client.get_order("RD-1", "a@example.com")
    assert "RD-1" not in json.dumps(str(caught.value))

    def not_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>")

    client, http = store_client(not_json)
    async with http:
        with pytest.raises(StoreContractError):
            await client.get_history("a@example.com")
