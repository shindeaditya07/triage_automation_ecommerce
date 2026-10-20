"""The drafting step over the in-memory policy index and a scripted drafter."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from services.api.context.lookup import ContextLookup, LookupResult
from services.api.llm.client import DraftRequest, LLMResult
from services.api.llm.errors import LLMInvalidOutputError, LLMRequestError
from services.api.llm.fake import FakeLLMClient
from services.api.models import Order
from services.api.models.draft import DraftOutput
from services.api.models.enums import Intent, TicketStatus
from services.api.pipeline.draft import (
    INVALID_OUTPUT_REASON,
    RETRIEVAL_FAILED_REASON,
    DraftingConfig,
    draft_ticket,
    order_for_model,
)
from services.api.pipeline.store import TicketText
from services.api.policy_source import policies_path
from services.api.retrieval.index import MemoryPolicyIndex
from tests.context.support import seeded
from tests.pipeline.test_classify_stage import good

pytestmark = pytest.mark.anyio

SIGN_OFF = "Kaveri Home Goods Support"
RECEIVED = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


class ScriptedDrafter(FakeLLMClient):
    """Replays drafts (or raises errors) in order, and keeps the requests it was given."""

    def __init__(self, script: list[Any]) -> None:
        super().__init__()
        self.script = list(script)
        self.requests: list[DraftRequest] = []

    async def draft(self, request: DraftRequest) -> LLMResult[DraftOutput]:
        self.requests.append(request)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResult(item, self._record("draft", "draft"))


def output(text: str = f"Hello.\n\n{SIGN_OFF}") -> DraftOutput:
    return DraftOutput(
        reply_text=text,
        confidence=0.8,
        prompt_version="1",
        prompt_hash="h",
        model="fake-recorded",
        policy_version="1.1",
    )


def an_order() -> Order:
    scenario = next(s for s in seeded().scenarios if s.case == 8)
    return next(o for o in seeded().orders if o.order_id == scenario.order_id)


def ticket(intent: Intent = Intent.RETURN) -> TicketText:
    return TicketText(
        uuid4(),
        TicketStatus.CLASSIFIED,
        "Return",
        "I want to return my order, it arrived broken.",
        RECEIVED,
        good().model_copy(update={"intent": intent}),
        customer_email="someone@example.com",
    )


def found() -> ContextLookup:
    order = an_order()
    return ContextLookup(LookupResult.FOUND, order.order_id, 1, order, None)


@pytest.fixture
async def config() -> DraftingConfig:
    index = await MemoryPolicyIndex.build(
        FakeLLMClient(), policies_path().read_text(encoding="utf-8")
    )
    return DraftingConfig(index, SIGN_OFF)


async def test_the_draft_is_made_from_the_order_and_the_retrieved_policy(
    config: DraftingConfig,
) -> None:
    client = ScriptedDrafter([output()])
    snapshot_id = uuid4()
    stage = await draft_ticket(client, config, ticket(), found(), snapshot_id=snapshot_id)
    assert stage.draft is not None and stage.escalation_reason is None
    assert stage.draft.order_snapshot_id == snapshot_id  # recorded from the step, not the model
    assert stage.draft.policy_version == config.index.version
    assert [c.purpose for c in stage.calls] == ["embed", "draft"]
    [request] = client.requests
    assert request.order is not None and request.order["order_id"] == an_order().order_id
    assert "customer_email" not in request.order  # the sender's address stays out
    assert request.order_snapshot_id == snapshot_id
    assert request.sign_off == SIGN_OFF and request.policy_version == config.index.version
    assert request.failures == []
    ids = [p.policy_id for p in request.passages]
    assert ids == stage.passage_ids and ids[0] == "TON-9.2" and "RET-1.1" in ids


async def test_no_order_means_no_order_record_in_the_request(config: DraftingConfig) -> None:
    client = ScriptedDrafter([output()])
    lookup = ContextLookup(LookupResult.NO_ORDER)
    await draft_ticket(client, config, ticket(Intent.ORDER_STATUS), lookup)
    assert client.requests[0].order is None and client.requests[0].order_snapshot_id is None
    assert order_for_model(lookup) is None


async def test_a_suggestion_only_draft_gets_the_outside_policy_passage(
    config: DraftingConfig,
) -> None:
    plain = ScriptedDrafter([output()])
    await draft_ticket(plain, config, ticket(), found())
    marked = ScriptedDrafter([output()])
    stage = await draft_ticket(marked, config, ticket(), found(), suggestion_only=True)
    assert "ESC-8.3" in stage.passage_ids
    assert "ESC-8.3" not in [p.policy_id for p in plain.requests[0].passages]


async def test_an_invalid_answer_is_retried_once_with_the_reason(config: DraftingConfig) -> None:
    client = ScriptedDrafter([LLMInvalidOutputError("confidence: out of range"), output()])
    stage = await draft_ticket(client, config, ticket(), found())
    assert stage.draft is not None
    assert client.requests[0].failures == []
    assert client.requests[1].failures == ["confidence: out of range"]


async def test_two_invalid_answers_escalate_and_keep_the_retrieval_cost(
    config: DraftingConfig,
) -> None:
    client = ScriptedDrafter([LLMInvalidOutputError("a"), LLMInvalidOutputError("b")])
    stage = await draft_ticket(client, config, ticket(), found())
    assert stage.draft is None and stage.escalation_reason == INVALID_OUTPUT_REASON
    assert [c.purpose for c in stage.calls] == ["embed"]


async def test_another_model_failure_escalates_with_its_reason(config: DraftingConfig) -> None:
    client = ScriptedDrafter([LLMRequestError(401)])
    stage = await draft_ticket(client, config, ticket(), found())
    assert stage.draft is None and stage.escalation_reason == "llm_error:LLMRequestError"
    assert len(client.requests) == 1


async def test_a_policy_index_missing_a_required_passage_escalates() -> None:
    empty = MemoryPolicyIndex("1.1", {}, {})
    stage = await draft_ticket(
        ScriptedDrafter([]), DraftingConfig(empty, SIGN_OFF), ticket(), found()
    )
    assert stage.draft is None and stage.escalation_reason == RETRIEVAL_FAILED_REASON


async def test_an_unclassified_ticket_cannot_be_drafted(config: DraftingConfig) -> None:
    bare = TicketText(uuid4(), TicketStatus.NEW, None, "hi", RECEIVED)
    with pytest.raises(ValueError, match="classified"):
        await draft_ticket(ScriptedDrafter([]), config, bare, found())
