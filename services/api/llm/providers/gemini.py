"""Google Gemini chat (`generateContent`) and embeddings (`batchEmbedContents`). Plain HTTPS, like
the other adapters.

Chat asks for a JSON reply with `responseMimeType` and puts the schema in the instructions, instead
of sending it as a response schema: Gemini accepts only part of JSON Schema there and rejects the
rest with a 400, while the gateway already checks every answer against the schema. Gemini 2.5 and
later models think before answering, and thinking tokens count against `maxOutputTokens`, so the
limit gets headroom or the JSON would be cut off. Thinking tokens are billed as output tokens and
are counted as such.

Gemini can return fewer dimensions than its native 3072, so the vectors are the width of the
`policy_embeddings` column. Google does not say reduced-size vectors are unit length, so they are
scaled to unit length here: cosine distance does not depend on it, but the in-memory index and the
"nearest to itself scores 1" checks do.
"""

import json
import math
from typing import Any

import httpx

from services.api.llm.client import EmbedKind
from services.api.llm.errors import LLMInvalidOutputError
from services.api.llm.providers.base import RawCompletion, post_json, token_count

BASE_URL = "https://generativelanguage.googleapis.com"
EMBED_BATCH = 50
THINKING_HEADROOM = 2048  # output tokens a model may spend thinking, on top of the answer
# Gemini embeds a stored passage and a search message differently; telling it which is which
# improves the match.
_TASK_TYPES: dict[EmbedKind, str] = {
    "document": "RETRIEVAL_DOCUMENT",
    "query": "RETRIEVAL_QUERY",
}


def _unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector


class GeminiChat:
    family = "gemini"

    def __init__(
        self, client: httpx.AsyncClient, api_key: str, model: str, base_url: str = BASE_URL
    ) -> None:
        self._client = client
        self._api_key = api_key
        self.model = model
        self._url = f"{base_url.rstrip('/')}/v1beta/models/{model}:generateContent"

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
        instructions = (
            f"{system}\n\nReply with one JSON object and nothing else. It must fit this JSON "
            f"Schema ({schema_name}):\n{json.dumps(schema)}"
        )
        config: dict[str, Any] = {
            "responseMimeType": "application/json",
            "maxOutputTokens": max_tokens + THINKING_HEADROOM,
        }
        if temperature is not None:
            config["temperature"] = temperature
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": instructions}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": config,
        }
        headers = {"x-goog-api-key": self._api_key, "content-type": "application/json"}
        payload = await post_json(self._client, self._url, headers=headers, body=body)
        candidates = payload.get("candidates")
        candidate = candidates[0] if isinstance(candidates, list) and candidates else None
        if not isinstance(candidate, dict):
            feedback = payload.get("promptFeedback")
            reason = feedback.get("blockReason") if isinstance(feedback, dict) else None
            detail = f" (blocked: {reason})" if isinstance(reason, str) and reason.isalnum() else ""
            raise LLMInvalidOutputError(f"the answer had no candidates{detail}")
        if candidate.get("finishReason") == "MAX_TOKENS":
            raise LLMInvalidOutputError("the answer was cut off at the token limit")
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        texts = [
            part["text"]
            for part in (parts if isinstance(parts, list) else [])
            if isinstance(part, dict)
            and isinstance(part.get("text"), str)
            and not part.get("thought")
        ]
        if not texts:
            raise LLMInvalidOutputError("the model declined or returned no content")
        try:
            data = json.loads("".join(texts))
        except ValueError:
            raise LLMInvalidOutputError("the answer was not valid JSON") from None
        if not isinstance(data, dict):
            raise LLMInvalidOutputError("the answer was not a JSON object")
        usage = payload.get("usageMetadata")
        return RawCompletion(
            data,
            token_count(usage, "promptTokenCount"),
            token_count(usage, "candidatesTokenCount") + token_count(usage, "thoughtsTokenCount"),
        )


class GeminiEmbedder:
    def __init__(
        self, client: httpx.AsyncClient, api_key: str, model: str, base_url: str = BASE_URL
    ) -> None:
        self._client = client
        self._api_key = api_key
        self.model = model
        self._url = f"{base_url.rstrip('/')}/v1beta/models/{model}:batchEmbedContents"

    async def embed(
        self, texts: list[str], dimensions: int, kind: EmbedKind
    ) -> tuple[list[list[float]], int]:
        vectors: list[list[float]] = []
        tokens = 0
        headers = {"x-goog-api-key": self._api_key, "content-type": "application/json"}
        for start in range(0, len(texts), EMBED_BATCH):
            batch = texts[start : start + EMBED_BATCH]
            body: dict[str, Any] = {
                "requests": [
                    {
                        "model": f"models/{self.model}",
                        "content": {"parts": [{"text": text}]},
                        "taskType": _TASK_TYPES[kind],
                        "outputDimensionality": dimensions,
                    }
                    for text in batch
                ]
            }
            payload = await post_json(self._client, self._url, headers=headers, body=body)
            embeddings = payload.get("embeddings")
            if not isinstance(embeddings, list) or len(embeddings) != len(batch):
                raise LLMInvalidOutputError("the embedding reply had the wrong number of vectors")
            for item in embeddings:
                values = item.get("values") if isinstance(item, dict) else None
                if not isinstance(values, list) or len(values) != dimensions:
                    raise LLMInvalidOutputError("an embedding had the wrong length")
                try:
                    vectors.append(_unit([float(x) for x in values]))
                except (TypeError, ValueError):
                    raise LLMInvalidOutputError("an embedding held a non-number") from None
            tokens += token_count(payload.get("usageMetadata"), "promptTokenCount")
        return vectors, tokens
