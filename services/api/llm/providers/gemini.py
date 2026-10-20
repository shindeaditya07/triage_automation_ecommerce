"""Google Gemini embeddings (`batchEmbedContents`). Plain HTTPS, like the other adapters.

Gemini can return fewer dimensions than its native 3072, so the vectors are the width of the
`policy_embeddings` column. Google does not say reduced-size vectors are unit length, so they are
scaled to unit length here: cosine distance does not depend on it, but the in-memory index and the
"nearest to itself scores 1" checks do.
"""

import math
from typing import Any

import httpx

from services.api.llm.client import EmbedKind
from services.api.llm.errors import LLMInvalidOutputError
from services.api.llm.providers.base import post_json, token_count

BASE_URL = "https://generativelanguage.googleapis.com"
EMBED_BATCH = 50
# Gemini embeds a stored passage and a search message differently; telling it which is which
# improves the match.
_TASK_TYPES: dict[EmbedKind, str] = {
    "document": "RETRIEVAL_DOCUMENT",
    "query": "RETRIEVAL_QUERY",
}


def _unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector


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
