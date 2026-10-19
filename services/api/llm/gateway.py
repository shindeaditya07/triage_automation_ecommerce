"""ModelGateway: the real `LLMClient`. It adds the prompt, the retries, the circuit breaker and
the cost record around a provider adapter, and checks every answer against our own schema.

A call goes: breaker check -> adapter (retried on transient errors) -> schema validation -> record.
Only a provider that could not be reached counts against the breaker; a provider that answered,
even with a refusal or unusable output, is up.
"""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ValidationError

from services.api.llm.client import (
    CallRecord,
    ClassifyRequest,
    DraftModelOutput,
    DraftRequest,
    JudgeRequest,
    LLMClient,
    LLMResult,
    Purpose,
)
from services.api.llm.errors import LLMError, LLMInvalidOutputError, LLMTransientError
from services.api.llm.pricing import Price
from services.api.llm.prompts import (
    CLASSIFY,
    DRAFT,
    JUDGE,
    PromptRegistry,
    classify_message,
    draft_message,
    judge_message,
)
from services.api.llm.providers.base import ChatModel, Embedder, RawCompletion
from services.api.llm.resilience import CircuitBreaker, RetryPolicy, with_retries
from services.api.models.draft import DraftOutput
from services.api.models.judge import JudgeOutput
from services.api.models.ticket import Classification

MAX_TOKENS: dict[str, int] = {CLASSIFY: 300, DRAFT: 1200, JUDGE: 600}


@dataclass
class ModelRoute:
    """One model, with the things that belong to it alone."""

    model: ChatModel
    price: Price
    breaker: CircuitBreaker
    temperature: float | None


def _field_errors(exc: ValidationError) -> str:
    """Which fields were wrong and why, without the values (they can hold customer text)."""
    return "; ".join(
        f"{'.'.join(str(p) for p in error['loc']) or 'output'}: {error['type']}"
        for error in exc.errors()
    )[:500]


class ModelGateway(LLMClient):
    def __init__(
        self,
        *,
        drafter: ModelRoute,
        judge: ModelRoute,
        embedder: Embedder,
        embed_price: Price,
        embed_breaker: CircuitBreaker,
        embed_dimensions: int,
        prompts: PromptRegistry,
        retry: RetryPolicy,
        http_client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._drafter = drafter
        self._judge = judge
        self._embedder = embedder
        self._embed_price = embed_price
        self._embed_breaker = embed_breaker
        self._dimensions = embed_dimensions
        self._prompts = prompts
        self._retry = retry
        self._http = http_client
        self._sleep = sleep
        self._rand = rand
        self._monotonic = monotonic

    async def _guarded[T](
        self, breaker: CircuitBreaker, call: Callable[[], Awaitable[T]]
    ) -> tuple[T, int]:
        """Run `call` through the breaker and the retries. Returns the result and the tries used."""
        breaker.before_call()
        try:
            result = await with_retries(call, self._retry, sleep=self._sleep, rand=self._rand)
        except LLMTransientError:
            breaker.record_failure()
            raise
        except LLMError:
            breaker.record_success()
            raise
        breaker.record_success()
        return result

    async def _structured[M: BaseModel](
        self,
        route: ModelRoute,
        purpose: Purpose,
        prompt_name: str,
        user: str,
        output_type: type[M],
    ) -> LLMResult[M]:
        prompt = self._prompts.get(prompt_name)
        schema = output_type.model_json_schema()
        started = self._monotonic()

        async def call() -> RawCompletion:
            return await route.model.complete(
                system=prompt.text,
                user=user,
                schema=schema,
                schema_name=prompt_name,
                max_tokens=MAX_TOKENS[prompt_name],
                temperature=route.temperature,
            )

        raw, attempts = await self._guarded(route.breaker, call)
        try:
            value = output_type.model_validate(raw.data)
        except ValidationError as exc:
            raise LLMInvalidOutputError(_field_errors(exc)) from None
        record = CallRecord(
            purpose=purpose,
            model=route.model.model,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            prompt_hash=prompt.sha256,
            temperature=route.temperature,
            input_tokens=raw.input_tokens,
            output_tokens=raw.output_tokens,
            latency_ms=round((self._monotonic() - started) * 1000),
            cost_inr=route.price.cost_inr(raw.input_tokens, raw.output_tokens),
            attempts=attempts,
        )
        return LLMResult(value, record)

    async def classify(self, request: ClassifyRequest) -> LLMResult[Classification]:
        return await self._structured(
            self._drafter, "classify", CLASSIFY, classify_message(request), Classification
        )

    async def draft(self, request: DraftRequest) -> LLMResult[DraftOutput]:
        result = await self._structured(
            self._drafter, "draft", DRAFT, draft_message(request), DraftModelOutput
        )
        out = result.value
        draft = DraftOutput(
            reply_text=out.reply_text,
            citations=out.citations,
            proposed_action=out.proposed_action,
            confidence=out.confidence,
            prompt_version=result.record.prompt_version or "",
            prompt_hash=result.record.prompt_hash or "",
            model=result.record.model,
            policy_version=request.policy_version,
            order_snapshot_id=request.order_snapshot_id,
        )
        return LLMResult(draft, result.record)

    async def judge(self, request: JudgeRequest) -> LLMResult[JudgeOutput]:
        return await self._structured(
            self._judge, "judge", JUDGE, judge_message(request), JudgeOutput
        )

    async def embed(self, texts: Sequence[str]) -> LLMResult[list[list[float]]]:
        started = self._monotonic()
        batch = list(texts)

        async def call() -> tuple[list[list[float]], int]:
            return await self._embedder.embed(batch, self._dimensions)

        (vectors, tokens), attempts = await self._guarded(self._embed_breaker, call)
        if len(vectors) != len(batch) or any(len(v) != self._dimensions for v in vectors):
            raise LLMInvalidOutputError("the embeddings did not match the request")
        record = CallRecord(
            purpose="embed",
            model=self._embedder.model,
            input_tokens=tokens,
            output_tokens=0,
            latency_ms=round((self._monotonic() - started) * 1000),
            cost_inr=self._embed_price.cost_inr(tokens),
            attempts=attempts,
        )
        return LLMResult(vectors, record)

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
