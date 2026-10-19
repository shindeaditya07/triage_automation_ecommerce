"""Anthropic Messages API. Structured output is a forced tool call whose input schema is ours."""

from typing import Any

import httpx

from services.api.llm.errors import LLMInvalidOutputError
from services.api.llm.providers.base import RawCompletion, post_json, token_count

BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"


class AnthropicChat:
    family = "anthropic"

    def __init__(
        self, client: httpx.AsyncClient, api_key: str, model: str, base_url: str = BASE_URL
    ) -> None:
        self._client = client
        self._api_key = api_key
        self.model = model
        self._url = f"{base_url.rstrip('/')}/v1/messages"

    async def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        max_tokens: int,
        temperature: float | None,
    ) -> RawCompletion:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "tools": [
                {
                    "name": schema_name,
                    "description": "Return the result as this object.",
                    "input_schema": schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": schema_name},
        }
        if temperature is not None:
            body["temperature"] = temperature
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        payload = await post_json(self._client, self._url, headers=headers, body=body)
        if payload.get("stop_reason") == "max_tokens":
            raise LLMInvalidOutputError("the answer was cut off at the token limit")
        for part in payload.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "tool_use":
                data = part.get("input")
                if isinstance(data, dict):
                    usage = payload.get("usage")
                    return RawCompletion(
                        data,
                        token_count(usage, "input_tokens"),
                        token_count(usage, "output_tokens"),
                    )
        raise LLMInvalidOutputError("the answer had no structured result")
