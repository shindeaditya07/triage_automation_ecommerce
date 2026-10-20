"""The policy index on real Postgres with pgvector: building it, searching it, and /readyz."""

from collections.abc import Sequence

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from scripts import index_policies
from services.api import health
from services.api.llm.client import LLMResult
from services.api.llm.fake import FakeLLMClient, embed_text
from services.api.models.enums import Intent
from services.api.policy_source import expectation, parse_passages, policies_path
from services.api.retrieval.index import PgPolicyIndex, build_index
from services.api.retrieval.retrieve import Source, retrieve

pytestmark = pytest.mark.anyio

POLICIES = policies_path().read_text(encoding="utf-8")
MODEL = FakeLLMClient.model


class Counting(FakeLLMClient):
    def __init__(self) -> None:
        super().__init__()
        self.embedded: list[str] = []

    async def embed(self, texts: Sequence[str]) -> LLMResult[list[list[float]]]:
        self.embedded.extend(texts)
        return await super().embed(texts)


@pytest.fixture
async def clean(engine: AsyncEngine):  # type: ignore[no-untyped-def]
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM policy_embeddings"))
    yield engine
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM policy_embeddings"))


async def rows(engine: AsyncEngine) -> dict[str, tuple[str, str]]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT policy_id, passage_text, embed_model FROM policy_embeddings")
        )
        return {r.policy_id: (r.passage_text, r.embed_model) for r in result}


async def test_building_embeds_every_passage_and_readiness_passes(clean: AsyncEngine) -> None:
    llm = Counting()
    result = await build_index(clean, llm, policies_text=POLICIES, embed_model=MODEL)
    assert (result.version, result.embedded, result.unchanged) == ("1.1", 47, 0)
    assert result.model == MODEL and len(llm.embedded) == 47
    assert set(await rows(clean)) == set(parse_passages(POLICIES))
    async with clean.connect() as conn:
        assert await health._check_policy_index(conn, expectation()) is None


async def test_building_again_changes_nothing_and_embeds_nothing(clean: AsyncEngine) -> None:
    await build_index(clean, FakeLLMClient(), policies_text=POLICIES, embed_model=MODEL)
    llm = Counting()
    result = await build_index(clean, llm, policies_text=POLICIES, embed_model=MODEL)
    assert (result.embedded, result.unchanged, result.model) == (0, 47, None)
    assert llm.embedded == [] and result.calls == []


async def test_a_changed_passage_is_the_only_one_re_embedded(clean: AsyncEngine) -> None:
    await build_index(clean, FakeLLMClient(), policies_text=POLICIES, embed_model=MODEL)
    edited = POLICIES.replace("Accepted payment methods: UPI", "Accepted payment methods: UPI,")
    llm = Counting()
    result = await build_index(clean, llm, policies_text=edited, embed_model=MODEL)
    assert (result.embedded, result.unchanged) == (1, 46)
    assert len(llm.embedded) == 1 and "UPI," in (await rows(clean))["PAY-7.1"][0]


async def test_a_different_embedding_model_re_embeds_everything_in_place(
    clean: AsyncEngine,
) -> None:
    await build_index(clean, FakeLLMClient(), policies_text=POLICIES, embed_model=MODEL)
    async with clean.begin() as conn:
        await conn.execute(text("UPDATE policy_embeddings SET embed_model = 'old-model'"))
    result = await build_index(clean, FakeLLMClient(), policies_text=POLICIES, embed_model=MODEL)
    assert result.embedded == 47
    assert {model for _, model in (await rows(clean)).values()} == {MODEL}
    assert len(await rows(clean)) == 47  # replaced, not duplicated


async def test_an_embedder_that_reports_another_model_name_is_refused(clean: AsyncEngine) -> None:
    with pytest.raises(ValueError, match="expected 'other'"):
        await build_index(clean, FakeLLMClient(), policies_text=POLICIES, embed_model="other")
    assert await rows(clean) == {}


async def test_a_new_policy_version_adds_rows_and_readiness_follows_the_file(
    clean: AsyncEngine,
) -> None:
    await build_index(clean, FakeLLMClient(), policies_text=POLICIES, embed_model=MODEL)
    newer = POLICIES.replace("Policy version **1.1**", "Policy version **1.2**")
    result = await build_index(clean, FakeLLMClient(), policies_text=newer, embed_model=MODEL)
    assert (result.version, result.embedded) == ("1.2", 47)
    async with clean.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM policy_embeddings")) == 94
        assert await health._check_policy_index(conn, expectation()) is None  # 1.1 still complete


async def test_the_nearest_passage_to_its_own_vector_is_itself(clean: AsyncEngine) -> None:
    await build_index(clean, FakeLLMClient(), policies_text=POLICIES, embed_model=MODEL)
    index = PgPolicyIndex(clean, "1.1")
    passage = parse_passages(POLICIES)["DMG-3.1"]
    [best, *_] = await index.nearest(embed_text(passage), 3, MODEL)
    assert best.policy_id == "DMG-3.1" and best.score == pytest.approx(1.0, abs=1e-4)
    assert await index.nearest(embed_text(passage), 3, "another-model") == []
    assert await PgPolicyIndex(clean, "9.9").nearest(embed_text(passage), 3, MODEL) == []


async def test_texts_come_back_by_id_and_unknown_ids_are_left_out(clean: AsyncEngine) -> None:
    await build_index(clean, FakeLLMClient(), policies_text=POLICIES, embed_model=MODEL)
    found = await PgPolicyIndex(clean, "1.1").texts(["RET-1.1", "NOPE-0.0"])
    assert list(found) == ["RET-1.1"] and "7 calendar days" in found["RET-1.1"]


async def test_retrieval_end_to_end_over_postgres(clean: AsyncEngine) -> None:
    llm = FakeLLMClient()
    await build_index(clean, llm, policies_text=POLICIES, embed_model=MODEL)
    result = await retrieve(
        PgPolicyIndex(clean, "1.1"),
        llm,
        subject="Gift wrapping",
        body="Do you offer gift wrapping and what does it cost?",
        intent=Intent.PRODUCT_QUESTION,
    )
    assert result.policy_ids[:2] == ["TON-9.2", "PRD-6.1"]
    assert result.passages[2].policy_id == "FAQ-10.5" and result.passages[2].source is Source.VECTOR


async def test_the_command_builds_the_index_from_the_environment(
    clean: AsyncEngine,
    migrated_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", migrated_url)
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    assert await index_policies.main() == 0
    assert "policy version 1.1: 47 embedded, 0 unchanged" in capsys.readouterr().out
    assert await index_policies.main() == 0
    assert "0 embedded, 47 unchanged" in capsys.readouterr().out
