"""The order store, as the pipeline sees it: ask for an order or a customer's history.

Every call has an explicit timeout. A store that cannot be reached is `StoreUnavailable` (the job
retries it); a store that answers with something outside the data contract is
`StoreContractError` (retrying cannot fix it). Error messages carry status codes only, never
response bodies or the customer's email.
"""

from enum import StrEnum
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ValidationError

from services.api.models import CustomerHistory, Order

# 4xx replies that mean "try again later" (request timeout, too many requests), not a bad request.
_RETRYABLE_CLIENT_STATUSES = frozenset({408, 429})


class StoreUnavailable(Exception):
    """The store did not answer, or failed (timeout, connection, 5xx, 408, 429). Try again."""


class StoreContractError(Exception):
    """The store answered, but not as docs/data-contract.md says it must."""


class OrderLookup(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    WRONG_CUSTOMER = "wrong_customer"


class OrderStoreClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")

    async def get_order(self, order_id: str, email: str) -> tuple[OrderLookup, Order | None]:
        """The order only if it belongs to `email`. A mismatch returns no order data."""
        response = await self._get(f"/orders/{quote(order_id, safe='')}", params={"email": email})
        if response.status_code == 200:
            return OrderLookup.FOUND, self._parse(response, Order)
        if response.status_code == 404:
            return OrderLookup.NOT_FOUND, None
        if response.status_code == 403:
            return OrderLookup.WRONG_CUSTOMER, None
        raise self._unexpected(response)

    async def get_history(self, email: str) -> CustomerHistory | None:
        response = await self._get(f"/customers/{quote(email, safe='')}/history")
        if response.status_code == 200:
            return self._parse(response, CustomerHistory)
        if response.status_code == 404:
            return None
        raise self._unexpected(response)

    async def _get(self, path: str, params: dict[str, str] | None = None) -> httpx.Response:
        try:
            return await self._http.get(f"{self._base}{path}", params=params)
        except httpx.TimeoutException:
            raise StoreUnavailable("the order store did not answer in time") from None
        except httpx.TransportError as exc:
            raise StoreUnavailable(
                f"could not reach the order store ({type(exc).__name__})"
            ) from None

    @staticmethod
    def _parse[M: BaseModel](response: httpx.Response, model: type[M]) -> M:
        try:
            return model.model_validate(response.json())
        except (ValueError, ValidationError):
            raise StoreContractError(
                f"the order store's {model.__name__} did not match the data contract"
            ) from None

    @staticmethod
    def _unexpected(response: httpx.Response) -> Exception:
        if response.status_code >= 500 or response.status_code in _RETRYABLE_CLIENT_STATUSES:
            return StoreUnavailable(f"the order store failed (HTTP {response.status_code})")
        return StoreContractError(
            f"unexpected reply from the order store (HTTP {response.status_code})"
        )
