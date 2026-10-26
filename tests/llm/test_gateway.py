from decimal import Decimal
from typing import Any

import pytest

from services.api.llm.client import ClassifyRequest, DraftRequest, JudgeRequest, Passage
from services.api.llm.errors import (
    CircuitOpenError,
    LLMInvalidOutputError,
    LLMRequestError,
    LLMServerError,
)
from services.api.llm.gateway import ModelGateway, ModelRoute
from services.api.llm.pricing import Price
from services.api.llm.prompts import PromptRegistry
from services.api.llm.providers.base import RawCompletion
from services.api.llm.resilience import CircuitBreaker, RetryPolicy
from services.api.models.enums import Intent, ProposedAction
from tests.llm.conftest import anyio_backend as anyio_backend

pytestmark = pytest.mark.anyio

GOOD_CLASSIFICATION = {
    "intent": "refund",
    "urgency": "low",
    "confidence": 0.9,
    "sentiment": "neutral",
    "flags": [],
}


class ScriptedModel:
    """Answers from a list; an exception in the list is raised instead."""

    family = "scripted"

    def __init__(self, model: str, script: list[Any]) -> None:
        self.model = model
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> RawCompletion:
        self.calls.append(kwargs)
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class ScriptedEmbedder:
    model = "emb"

    def __init__(self, width: int = 1536, count: int | None = None) -> None:
        self._width = width
        self._count = count
        self.kinds: list[str] = []

    async def embed(
        self, texts: list[str], dimensions: int, kind: str
    ) -> tuple[list[list[float]], int]:
        self.kinds.append(kind)
        n = len(texts) if self._count is None else self._count
        return [[0.5] * self._width for _ in range(n)], 40


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.25
        return self.now


def make(
    drafter: ScriptedModel,
    judge: ScriptedModel | None = None,
    *,
    embedder: ScriptedEmbedder | None = None,
    breaker_failures: int = 5,
    attempts: int = 3,
) -> tuple[ModelGateway, list[float]]:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    def route(model: ScriptedModel, price: Price) -> ModelRoute:
        return ModelRoute(model, price, CircuitBreaker(breaker_failures, 30.0, Clock()), 0.0)

    gateway = ModelGateway(
        drafter=route(drafter, Price(Decimal("1000"), Decimal("2000"))),
        judge=route(judge or ScriptedModel("j", []), Price(Decimal("500"), Decimal("500"))),
        embedder=embedder or ScriptedEmbedder(),
        embed_price=Price(Decimal("100")),
        embed_breaker=CircuitBreaker(breaker_failures, 30.0, Clock()),
        embed_dimensions=1536,
        prompts=PromptRegistry.load(),
        retry=RetryPolicy(max_attempts=attempts),
        sleep=sleep,
        rand=lambda: 0.0,
        monotonic=Clock(),
    )
    return gateway, slept


REQUEST = ClassifyRequest(ticket_id="t1", body="my parcel is late")


async def test_classify_validates_and_records_prompt_cost_and_latency() -> None:
    model = ScriptedModel("drafter-1", [RawCompletion(GOOD_CLASSIFICATION, 2000, 500)])
    gateway, _ = make(model)
    result = await gateway.classify(REQUEST)
    assert result.value.intent is Intent.REFUND
    record = result.record
    prompt = PromptRegistry.load().get("classify")
    assert (record.purpose, record.model, record.attempts) == ("classify", "drafter-1", 1)
    assert (record.prompt_name, record.prompt_version) == ("classify", prompt.version)
    assert record.prompt_hash == prompt.sha256
    assert record.cost_inr == Decimal("3.0000")  # (2000*1000 + 500*2000) / 1e6
    assert record.latency_ms == 250 and record.temperature == 0.0
    call = model.calls[0]
    assert call["system"] == prompt.text and call["max_tokens"] == 300
    assert "my parcel is late" in call["user"]
    assert record.audit_payload()["cost_inr"] == "3.0000"


async def test_output_that_fails_the_schema_raises_without_echoing_values() -> None:
    bad = {**GOOD_CLASSIFICATION, "intent": "SECRET-CUSTOMER-TEXT", "extra": 1}
    gateway, slept = make(ScriptedModel("m", [RawCompletion(bad, 1, 1)]))
    with pytest.raises(LLMInvalidOutputError) as caught:
        await gateway.classify(REQUEST)
    assert "intent" in caught.value.detail and "extra" in caught.value.detail
    assert "SECRET-CUSTOMER-TEXT" not in caught.value.detail
    assert slept == []  # invalid output is not retried here: the caller decides


async def test_transient_errors_are_retried_and_counted() -> None:
    model = ScriptedModel(
        "m", [LLMServerError("x"), LLMServerError("x"), RawCompletion(GOOD_CLASSIFICATION, 1, 1)]
    )
    gateway, slept = make(model)
    result = await gateway.classify(REQUEST)
    assert result.record.attempts == 3 and len(slept) == 2


async def test_a_4xx_is_not_retried_and_does_not_trip_the_breaker() -> None:
    model = ScriptedModel("m", [LLMRequestError(401)] * 3)
    gateway, slept = make(model, breaker_failures=2)
    for _ in range(3):
        with pytest.raises(LLMRequestError):
            await gateway.classify(REQUEST)
    assert len(model.calls) == 3 and slept == []


async def test_the_breaker_trips_after_n_failed_calls_then_fails_fast() -> None:
    model = ScriptedModel("m", [LLMServerError("x")] * 4)
    gateway, _ = make(model, breaker_failures=2, attempts=2)
    for _ in range(2):
        with pytest.raises(LLMServerError):
            await gateway.classify(REQUEST)
    assert len(model.calls) == 4  # 2 calls x 2 tries
    with pytest.raises(CircuitOpenError) as caught:
        await gateway.classify(REQUEST)
    assert caught.value.escalation_reason == "llm_error:CircuitOpenError"
    assert len(model.calls) == 4  # nothing was sent


async def test_draft_fills_in_what_the_model_does_not_decide() -> None:
    answer = {
        "reply_text": "Hello.",
        "citations": [{"claim": "30 days", "source_type": "policy", "source_ref": "RET-1.1"}],
        "proposed_action": "none",
        "confidence": 0.8,
    }
    model = ScriptedModel("d", [RawCompletion(answer, 100, 50)])
    gateway, _ = make(model)
    request = DraftRequest(
        ticket_id="t1",
        body="refund?",
        passages=[Passage(policy_id="RET-1.1", text="30 days")],
        policy_version="1.1",
        sign_off="Kaveri",
    )
    result = await gateway.draft(request)
    prompt = PromptRegistry.load().get("draft")
    draft = result.value
    assert draft.prompt_hash == prompt.sha256 and draft.prompt_version == prompt.version
    assert (draft.model, draft.policy_version) == ("d", "1.1")
    assert draft.proposed_action is ProposedAction.NONE
    assert model.calls[0]["max_tokens"] == 1200


async def test_a_model_cannot_set_the_prompt_hash_itself() -> None:
    answer = {"reply_text": "x", "confidence": 0.5, "prompt_hash": "forged"}
    gateway, _ = make(ScriptedModel("d", [RawCompletion(answer, 1, 1)]))
    request = DraftRequest(ticket_id="t", body="b", policy_version="1", sign_off="s")
    with pytest.raises(LLMInvalidOutputError):
        await gateway.draft(request)


async def test_judge_uses_its_own_model_and_enforces_the_verdict_rule() -> None:
    judge = ScriptedModel(
        "j1",
        [
            RawCompletion({"grounded": True, "issues": [], "confidence": 0.9}, 10, 5),
            RawCompletion(
                {"grounded": True, "issues": [{"claim": "c", "detail": "d"}], "confidence": 1}, 1, 1
            ),
        ],
    )
    drafter = ScriptedModel("d", [])
    gateway, _ = make(drafter, judge)
    request = JudgeRequest(reply_text="Hi")
    result = await gateway.judge(request)
    assert result.value.grounded and result.record.model == "j1"
    assert result.record.cost_inr == Decimal("0.0075")
    assert drafter.calls == [] and judge.calls[0]["max_tokens"] == 600
    with pytest.raises(LLMInvalidOutputError):
        await gateway.judge(request)


async def test_each_model_has_its_own_breaker() -> None:
    drafter = ScriptedModel("d", [LLMServerError("x")])
    judge = ScriptedModel(
        "j", [RawCompletion({"grounded": True, "issues": [], "confidence": 1}, 1, 1)]
    )
    gateway, _ = make(drafter, judge, breaker_failures=1, attempts=1)
    with pytest.raises(LLMServerError):
        await gateway.classify(REQUEST)
    with pytest.raises(CircuitOpenError):
        await gateway.classify(REQUEST)
    assert (await gateway.judge(JudgeRequest(reply_text="Hi"))).value.grounded


async def test_embed_records_tokens_and_cost() -> None:
    gateway, _ = make(ScriptedModel("d", []))
    result = await gateway.embed(["a", "b"])
    assert len(result.value) == 2 and len(result.value[0]) == 1536
    assert (result.record.purpose, result.record.input_tokens) == ("embed", 40)
    assert result.record.cost_inr == Decimal("0.0040")


async def test_embed_tells_the_provider_whether_it_is_a_passage_or_a_query() -> None:
    embedder = ScriptedEmbedder()
    gateway, _ = make(ScriptedModel("d", []), embedder=embedder)
    await gateway.embed(["a"])
    await gateway.embed(["b"], kind="query")
    assert embedder.kinds == ["document", "query"]


@pytest.mark.parametrize("embedder", [ScriptedEmbedder(width=8), ScriptedEmbedder(count=1)])
async def test_embeddings_of_the_wrong_shape_are_rejected(embedder: ScriptedEmbedder) -> None:
    gateway, _ = make(ScriptedModel("d", []), embedder=embedder)
    with pytest.raises(LLMInvalidOutputError):
        await gateway.embed(["a", "b"])
