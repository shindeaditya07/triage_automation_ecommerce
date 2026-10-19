"""FakeLLMClient: an `LLMClient` that makes no network call, so CI never reaches a live model.

`classify` replays recorded classifications (the eval corpus). `draft` and `judge` replay outputs
registered by a test, and fail closed when there is none. `embed` is a deterministic bag-of-words
hash, so similar texts get similar vectors without a model.
"""

import hashlib
import json
import math
import re
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from services.api.llm.client import (
    CallRecord,
    ClassifyRequest,
    DraftRequest,
    JudgeRequest,
    LLMClient,
    LLMResult,
    Purpose,
)
from services.api.llm.prompts import PromptRegistry
from services.api.models.draft import DraftOutput
from services.api.models.enums import Flag, Intent, Sentiment, Urgency
from services.api.models.judge import JudgeOutput
from services.api.models.records import EMBEDDING_DIMENSIONS
from services.api.models.ticket import Classification

_WORD = re.compile(r"[a-z0-9]+")


class RecordedOutputMissing(LookupError):
    """No recorded output for a request. Fails closed: a missing recording is never a guess."""


def embed_text(text: str, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    """A unit-length hashed bag-of-words vector. Same text, same vector; shared words, nearby."""
    vector = [0.0] * dimensions
    for word in _WORD.findall(text.lower()):
        digest = hashlib.sha256(word.encode()).digest()
        vector[int.from_bytes(digest[:4], "big") % dimensions] += 1.0
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector


class FakeLLMClient(LLMClient):
    model = "fake-recorded"
    temperature = 0.0

    def __init__(
        self,
        classifications: dict[str, Classification] | None = None,
        *,
        drafts: dict[str, DraftOutput] | None = None,
        judgements: dict[str, JudgeOutput] | None = None,
        prompts: PromptRegistry | None = None,
    ) -> None:
        self._classifications = dict(classifications or {})
        self._drafts = dict(drafts or {})
        self._judgements = dict(judgements or {})
        self._prompts = prompts

    @classmethod
    def from_jsonl(cls, path: Path) -> "FakeLLMClient":
        """Load `{"ticket_id": ..., "classification": {...}}` rows (extra keys are ignored)."""
        recorded: dict[str, Classification] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            body = row["classification"]
            recorded[row["ticket_id"]] = Classification(
                intent=Intent(body["intent"]),
                urgency=Urgency(body["urgency"]),
                confidence=body["confidence"],
                sentiment=Sentiment(body["sentiment"]),
                flags=[Flag(flag) for flag in body.get("flags", [])],
            )
        return cls(recorded)

    def recorded_classification(self, ticket_id: str) -> Classification:
        try:
            return self._classifications[ticket_id]
        except KeyError:
            raise RecordedOutputMissing(f"no recorded classification for {ticket_id!r}") from None

    def _record(self, purpose: Purpose, prompt_name: str | None, tokens: int = 0) -> CallRecord:
        prompt = self._prompts.get(prompt_name) if self._prompts and prompt_name else None
        return CallRecord(
            purpose=purpose,
            model=self.model,
            prompt_name=prompt.name if prompt else None,
            prompt_version=prompt.version if prompt else None,
            prompt_hash=prompt.sha256 if prompt else None,
            temperature=self.temperature,
            input_tokens=tokens,
            output_tokens=0,
            latency_ms=0,
            cost_inr=Decimal(0),
        )

    async def classify(self, request: ClassifyRequest) -> LLMResult[Classification]:
        return LLMResult(
            self.recorded_classification(request.ticket_id), self._record("classify", "classify")
        )

    async def draft(self, request: DraftRequest) -> LLMResult[DraftOutput]:
        try:
            output = self._drafts[request.ticket_id]
        except KeyError:
            raise RecordedOutputMissing(f"no recorded draft for {request.ticket_id!r}") from None
        return LLMResult(output, self._record("draft", "draft"))

    async def judge(self, request: JudgeRequest) -> LLMResult[JudgeOutput]:
        try:
            output = self._judgements[request.reply_text]
        except KeyError:
            raise RecordedOutputMissing("no recorded judgement for this reply") from None
        return LLMResult(output, self._record("judge", "judge"))

    async def embed(self, texts: Sequence[str]) -> LLMResult[list[list[float]]]:
        return LLMResult([embed_text(t) for t in texts], self._record("embed", None, len(texts)))
