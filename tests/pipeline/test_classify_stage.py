from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import pytest

from services.api.llm.client import (
    CallRecord,
    ClassifyRequest,
    DraftRequest,
    JudgeRequest,
    LLMClient,
    LLMResult,
)
from services.api.llm.errors import CircuitOpenError, LLMInvalidOutputError, LLMRequestError
from services.api.models.enums import Flag, Intent, Sentiment, Urgency
from services.api.models.ticket import Classification
from services.api.pipeline.classify import INVALID_OUTPUT_REASON, classify_ticket

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def good(flags: list[Flag] | None = None) -> Classification:
    return Classification(
        intent=Intent.REFUND,
        urgency=Urgency.NORMAL,
        confidence=0.9,
        sentiment=Sentiment.NEUTRAL,
        flags=flags or [],
    )


class Scripted(LLMClient):
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.requests: list[ClassifyRequest] = []

    async def classify(self, request: ClassifyRequest) -> LLMResult[Classification]:
        self.requests.append(request)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        record = CallRecord(
            purpose="classify",
            model="m",
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            cost_inr=Decimal(0),
        )
        return LLMResult(item, record)

    async def draft(self, request: DraftRequest) -> Any:
        raise NotImplementedError

    async def judge(self, request: JudgeRequest) -> Any:
        raise NotImplementedError

    async def embed(self, texts: Sequence[str], kind: str = "document") -> Any:
        raise NotImplementedError


async def test_a_valid_answer_is_returned_with_its_call_record() -> None:
    client = Scripted([good([Flag.ANGRY])])
    stage = await classify_ticket(client, "t1", "Subject", "Body")
    assert stage.classification is not None and stage.escalation_reason is None
    assert stage.classification.flags == [Flag.ANGRY]
    assert len(stage.calls) == 1 and stage.calls[0].purpose == "classify"
    assert (client.requests[0].subject, client.requests[0].body) == ("Subject", "Body")
    assert client.requests[0].previous_error is None


async def test_the_model_cannot_set_high_value_and_repeat_flags_collapse() -> None:
    client = Scripted([good([Flag.HIGH_VALUE, Flag.ANGRY, Flag.ANGRY])])
    stage = await classify_ticket(client, "t1", None, "Body")
    assert stage.classification is not None
    assert stage.classification.flags == [Flag.ANGRY]
    assert stage.dropped_flags == ["high_value"]


async def test_an_invalid_answer_is_retried_once_with_the_field_errors() -> None:
    client = Scripted([LLMInvalidOutputError("intent: enum"), good()])
    stage = await classify_ticket(client, "t1", None, "Body")
    assert stage.classification is not None
    assert [r.previous_error for r in client.requests] == [None, "intent: enum"]


async def test_two_invalid_answers_escalate() -> None:
    client = Scripted([LLMInvalidOutputError("a"), LLMInvalidOutputError("b")])
    stage = await classify_ticket(client, "t1", None, "Body")
    assert stage.classification is None
    assert stage.escalation_reason == INVALID_OUTPUT_REASON
    assert len(client.requests) == 2


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (CircuitOpenError("down"), "llm_error:CircuitOpenError"),
        (LLMRequestError(401), "llm_error:LLMRequestError"),
    ],
)
async def test_other_model_failures_escalate_without_a_second_try(
    error: Exception, reason: str
) -> None:
    client = Scripted([error, good()])
    stage = await classify_ticket(client, "t1", None, "Body")
    assert (stage.classification, stage.escalation_reason) == (None, reason)
    assert len(client.requests) == 1


async def test_a_provider_failure_keeps_its_own_message_for_the_owner() -> None:
    stage = await classify_ticket(Scripted([LLMRequestError(404, "NOT_FOUND")]), "t1", None, "Body")
    assert stage.error == "the provider rejected the request: HTTP 404 (NOT_FOUND)"
