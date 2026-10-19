import math

import httpx
import pytest
from pydantic import SecretStr

from evals import corpus
from services.api.llm.client import ClassifyRequest, DraftRequest, JudgeRequest
from services.api.llm.errors import LLMConfigError
from services.api.llm.factory import build_llm_client
from services.api.llm.fake import FakeLLMClient, RecordedOutputMissing, embed_text
from services.api.llm.gateway import ModelGateway
from services.api.models.records import EMBEDDING_DIMENSIONS
from services.api.settings import LLMSettings

pytestmark = pytest.mark.anyio


async def test_fake_classify_replays_a_recording_and_costs_nothing() -> None:
    client = FakeLLMClient.from_jsonl(corpus.RECORDED_PATH)
    first = corpus.load_recorded()[0]
    result = await client.classify(ClassifyRequest(ticket_id=first.ticket_id, body="x"))
    assert result.value.intent == first.classification.intent
    assert result.record.model == "fake-recorded" and result.record.cost_inr == 0
    with pytest.raises(RecordedOutputMissing):
        await client.classify(ClassifyRequest(ticket_id="T999", body="x"))


async def test_fake_draft_and_judge_fail_closed_without_a_recording() -> None:
    client = FakeLLMClient()
    with pytest.raises(RecordedOutputMissing):
        await client.draft(DraftRequest(ticket_id="t", body="b", policy_version="1", sign_off="s"))
    with pytest.raises(RecordedOutputMissing):
        await client.judge(JudgeRequest(reply_text="r"))


async def test_fake_embeddings_are_deterministic_and_similar_for_shared_words() -> None:
    client = FakeLLMClient()
    result = await client.embed(["refund within 30 days", "refund within 30 days", "shipping fee"])
    a, b, c = result.value
    assert a == b and len(a) == EMBEDDING_DIMENSIONS
    assert math.isclose(sum(x * x for x in a), 1.0)
    dot = sum(x * y for x, y in zip(a, c, strict=True))
    assert dot < 0.5
    assert embed_text("") == [0.0] * EMBEDDING_DIMENSIONS


def test_the_factory_gives_a_fake_by_default() -> None:
    assert isinstance(build_llm_client(LLMSettings()), FakeLLMClient)


def real_settings(**overrides: object) -> LLMSettings:
    values: dict[str, object] = {
        "llm_provider": "anthropic",
        "llm_api_key": SecretStr("k1"),  # pragma: allowlist secret
        "llm_model": "drafter",
        "judge_provider": "openai",
        "judge_api_key": SecretStr("k2"),  # pragma: allowlist secret
        "judge_model": "judge",
    }
    return LLMSettings(**(values | overrides))  # type: ignore[arg-type]


async def test_the_factory_builds_a_gateway_that_uses_the_given_client() -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    client = build_llm_client(real_settings(), http)
    assert isinstance(client, ModelGateway)
    await client.aclose()
    assert not http.is_closed  # it was lent to the gateway, so the gateway does not close it
    await http.aclose()


async def test_the_factory_owns_and_closes_a_client_it_made() -> None:
    client = build_llm_client(real_settings())
    assert isinstance(client, ModelGateway)
    await client.aclose()
    await client.aclose()


def test_the_factory_refuses_incomplete_settings() -> None:
    settings = LLMSettings.model_construct(llm_provider="anthropic")
    with pytest.raises(LLMConfigError):
        build_llm_client(settings)
