"""What every adapter provides, and the HTTP handling they share."""

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from services.api.llm.errors import (
    LLMConnectionError,
    LLMInvalidOutputError,
    LLMRateLimitError,
    LLMRequestError,
    LLMServerError,
    LLMTimeoutError,
)

SERVER_ERROR_STATUSES = frozenset({500, 502, 503, 504, 529})  # 529: Anthropic "overloaded"


@dataclass(frozen=True)
class RawCompletion:
    """A model's answer before it is checked against a schema."""

    data: dict[str, Any]
    input_tokens: int
    output_tokens: int


class ChatModel(Protocol):
    """One model that answers a prompt with a JSON object fitting a schema."""

    model: str
    family: str

    async def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        max_tokens: int,
        temperature: float | None,
    ) -> RawCompletion: ...


class Embedder(Protocol):
    model: str

    async def embed(self, texts: list[str], dimensions: int) -> tuple[list[list[float]], int]:
        """Vectors in the order of `texts`, and the input tokens used."""
        ...


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after", "")
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _error_type(response: httpx.Response) -> str | None:
    """The provider's short error category (e.g. 'invalid_request_error'), never its message,
    which can quote the request."""
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    kind = error.get("type") if isinstance(error, dict) else None
    return kind[:60] if isinstance(kind, str) and kind.replace("_", "").isalnum() else None


async def post_json(
    client: httpx.AsyncClient, url: str, *, headers: dict[str, str], body: dict[str, Any]
) -> dict[str, Any]:
    """POST JSON and return the JSON reply, mapping every failure to an `LLMError`."""
    try:
        response = await client.post(url, headers=headers, content=json.dumps(body))
    except httpx.TimeoutException:
        raise LLMTimeoutError("the model provider did not answer in time") from None
    except httpx.TransportError as exc:
        raise LLMConnectionError(
            f"could not reach the model provider ({type(exc).__name__})"
        ) from None
    status = response.status_code
    if status == 429:
        raise LLMRateLimitError("the model provider is rate limiting", _retry_after(response))
    if status in SERVER_ERROR_STATUSES:
        raise LLMServerError(f"the model provider failed (HTTP {status})", _retry_after(response))
    if status >= 400:
        raise LLMRequestError(status, _error_type(response))
    try:
        payload = response.json()
    except ValueError:
        raise LLMInvalidOutputError("the provider's reply was not JSON") from None
    if not isinstance(payload, dict):
        raise LLMInvalidOutputError("the provider's reply was not a JSON object")
    return payload


def token_count(usage: object, key: str) -> int:
    value = usage.get(key) if isinstance(usage, dict) else None
    return value if isinstance(value, int) and value >= 0 else 0
