"""Retrieval over the in-memory index, with the fake client's hashed bag-of-words vectors."""

from collections.abc import Sequence

import pytest

from services.api.llm.client import LLMResult
from services.api.llm.errors import LLMRequestError
from services.api.llm.fake import FakeLLMClient
from services.api.models.enums import Intent
from services.api.policy_source import policies_path
from services.api.retrieval.index import Hit, MemoryPolicyIndex
from services.api.retrieval.retrieve import (
    MAX_QUERY_CHARS,
    RetrievalError,
    Source,
    query_text,
    retrieve,
)

pytestmark = pytest.mark.anyio

POLICIES = policies_path().read_text(encoding="utf-8")


@pytest.fixture
async def index() -> MemoryPolicyIndex:
    return await MemoryPolicyIndex.build(FakeLLMClient(), POLICIES)


async def test_the_intents_passages_come_first_then_the_closest_by_meaning(
    index: MemoryPolicyIndex,
) -> None:
    result = await retrieve(
        index,
        FakeLLMClient(),
        subject="Gift wrapping",
        body="Do you offer gift wrapping and what does it cost?",
        intent=Intent.PRODUCT_QUESTION,
    )
    assert result.policy_ids[:2] == ["TON-9.2", "PRD-6.1"]
    assert all(p.source is Source.INTENT and p.score is None for p in result.passages[:2])
    vector = result.passages[2:]
    assert vector and all(p.source is Source.VECTOR and p.score is not None for p in vector)
    assert vector[0].policy_id == "FAQ-10.5"
    assert [p.score for p in vector] == sorted((p.score for p in vector), reverse=True)  # type: ignore[type-var]


async def test_a_passage_is_never_listed_twice(index: MemoryPolicyIndex) -> None:
    result = await retrieve(
        index,
        FakeLLMClient(),
        subject=None,
        body="I want to return this item, what is the return window?",
        intent=Intent.RETURN,
    )
    assert len(result.policy_ids) == len(set(result.policy_ids))
    assert {"RET-1.1", "RET-1.5"} <= set(result.policy_ids)


async def test_the_text_is_the_passage_from_the_policy_file(index: MemoryPolicyIndex) -> None:
    result = await retrieve(
        index, FakeLLMClient(), subject=None, body="Hello", intent=Intent.WARRANTY
    )
    by_id = {p.policy_id: p.text for p in result.passages}
    assert "6-month" in by_id["PRD-6.3"]
    assert result.policy_version == "1.1"
    assert result.call.purpose == "embed"


async def test_extra_passages_are_added_for_a_suggestion_only_draft(
    index: MemoryPolicyIndex,
) -> None:
    result = await retrieve(
        index,
        FakeLLMClient(),
        subject=None,
        body="Please let me return it, I was travelling.",
        intent=Intent.RETURN,
        extra_ids=["ESC-8.3"],
    )
    assert "ESC-8.3" in result.policy_ids
    assert result.passages[result.policy_ids.index("ESC-8.3")].source is Source.INTENT


async def test_top_k_zero_gives_only_the_required_passages(index: MemoryPolicyIndex) -> None:
    result = await retrieve(
        index,
        FakeLLMClient(),
        subject=None,
        body="Where is it",
        intent=Intent.CANCELLATION,
        top_k=0,
    )
    assert result.policy_ids == ["TON-9.2", "CAN-5.1", "CAN-5.2", "CAN-5.3"]


async def test_a_name_that_is_not_a_policy_passage_fails_closed(index: MemoryPolicyIndex) -> None:
    with pytest.raises(RetrievalError, match="ZZZ-9.9"):
        await retrieve(
            index,
            FakeLLMClient(),
            subject=None,
            body="hi",
            intent=Intent.RETURN,
            extra_ids=["ZZZ-9.9"],
        )


async def test_a_required_passage_missing_from_the_index_fails_closed() -> None:
    llm = FakeLLMClient()
    stale = await MemoryPolicyIndex.build(llm, POLICIES)
    stale._passages.pop("DMG-3.4")  # the index was built before this passage existed
    with pytest.raises(RetrievalError, match="DMG-3.4"):
        await retrieve(stale, llm, subject=None, body="It broke", intent=Intent.DAMAGED_ITEM)


async def test_a_model_failure_is_raised_for_the_caller_to_escalate(
    index: MemoryPolicyIndex,
) -> None:
    class Down(FakeLLMClient):
        async def embed(self, texts: Sequence[str]) -> LLMResult[list[list[float]]]:
            raise LLMRequestError(401)

    with pytest.raises(LLMRequestError):
        await retrieve(index, Down(), subject=None, body="hi", intent=Intent.RETURN)


async def test_the_query_is_embedded_once_and_looked_up_with_the_models_name() -> None:
    seen: list[tuple[int, str]] = []

    class Spy(MemoryPolicyIndex):
        async def nearest(self, vector: Sequence[float], limit: int, model: str) -> list[Hit]:
            seen.append((limit, model))
            return await super().nearest(vector, limit, model)

    llm = FakeLLMClient()
    spy = Spy("1.1", {}, {})
    spy._passages = (await MemoryPolicyIndex.build(llm, POLICIES))._passages
    await retrieve(spy, llm, subject=None, body="hi", intent=Intent.PAYMENT, top_k=3)
    assert seen == [(3 + 4, "fake-recorded")]  # top_k plus the four required passages


def test_the_query_joins_subject_and_body_and_is_capped() -> None:
    assert query_text("Subject", "Body") == "Subject\nBody"
    assert query_text(None, "Body") == "Body"
    assert len(query_text("s", "x" * 10 * MAX_QUERY_CHARS)) == MAX_QUERY_CHARS
