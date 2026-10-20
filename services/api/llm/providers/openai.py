"""OpenAI Chat Completions (structured output as a JSON schema) and Embeddings."""

import json
from typing import Any

import httpx

from services.api.llm.client import EmbedKind
from services.api.llm.errors import LLMInvalidOutputError
from services.api.llm.providers.base import RawCompletion, post_json, token_count

BASE_URL = "https://api.openai.com"
EMBED_BATCH = 100


class OpenAIChat:
    family = "openai"

    def __init__(
        self, client: httpx.AsyncClient, api_key: str, model: str, base_url: str = BASE_URL
    ) -> None:
        self._client = client
        self._api_key = api_key
        self.model = model
        self._url = f"{base_url.rstrip('/')}/v1/chat/completions"

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
            "max_completion_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": False},
            },
        }
        if temperature is not None:
            body["temperature"] = temperature
        headers = {"authorization": f"Bearer {self._api_key}", "content-type": "application/json"}
        payload = await post_json(self._client, self._url, headers=headers, body=body)
        choices = payload.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else None
        if not isinstance(choice, dict):
            raise LLMInvalidOutputError("the answer had no choices")
        if choice.get("finish_reason") == "length":
            raise LLMInvalidOutputError("the answer was cut off at the token limit")
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise LLMInvalidOutputError("the model declined or returned no content")
        try:
            data = json.loads(content)
        except ValueError:
            raise LLMInvalidOutputError("the answer was not valid JSON") from None
        if not isinstance(data, dict):
            raise LLMInvalidOutputError("the answer was not a JSON object")
        usage = payload.get("usage")
        return RawCompletion(
            data, token_count(usage, "prompt_tokens"), token_count(usage, "completion_tokens")
        )


class OpenAIEmbedder:
    def __init__(
        self, client: httpx.AsyncClient, api_key: str, model: str, base_url: str = BASE_URL
    ) -> None:
        self._client = client
        self._api_key = api_key
        self.model = model
        self._url = f"{base_url.rstrip('/')}/v1/embeddings"

    async def embed(
        self, texts: list[str], dimensions: int, kind: EmbedKind
    ) -> tuple[list[list[float]], int]:
        vectors: list[list[float]] = []
        tokens = 0
        headers = {"authorization": f"Bearer {self._api_key}", "content-type": "application/json"}
        for start in range(0, len(texts), EMBED_BATCH):
            batch = texts[start : start + EMBED_BATCH]
            body = {
                "model": self.model,
                "input": batch,
                "dimensions": dimensions,
                "encoding_format": "float",
            }
            payload = await post_json(self._client, self._url, headers=headers, body=body)
            data = payload.get("data")
            if not isinstance(data, list) or len(data) != len(batch):
                raise LLMInvalidOutputError("the embedding reply had the wrong number of vectors")
            ordered = sorted(
                (item for item in data if isinstance(item, dict)),
                key=lambda item: item.get("index", 0),
            )
            for item in ordered:
                vector = item.get("embedding")
                if not isinstance(vector, list) or len(vector) != dimensions:
                    raise LLMInvalidOutputError("an embedding had the wrong length")
                vectors.append([float(x) for x in vector])
            tokens += token_count(payload.get("usage"), "prompt_tokens")
        return vectors, tokens
