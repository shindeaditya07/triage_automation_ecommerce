"""Grounding inside the drafting step: the retry with reasons and the fallback template."""

import dataclasses

import pytest

from services.api.grounding.config import default_config
from services.api.llm.errors import LLMInvalidOutputError, LLMRequestError
from services.api.llm.fake import FakeLLMClient
from services.api.models.enums import DraftKind
from services.api.models.judge import JudgeIssue, JudgeOutput
from services.api.pipeline.draft import (
    FALLBACK_INVALID_REASON,
    FALLBACK_MODEL,
    GROUNDING_UNAVAILABLE_REASON,
    INVALID_OUTPUT_REASON,
    DraftingConfig,
    draft_ticket,
)
from services.api.policy_source import policies_path
from services.api.retrieval.index import MemoryPolicyIndex
from tests.pipeline.test_draft_stage import (
    SIGN_OFF,
    ScriptedDrafter,
    found,
    output,
    ticket,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
async def config() -> DraftingConfig:
    index = await MemoryPolicyIndex.build(
        FakeLLMClient(), policies_path().read_text(encoding="utf-8")
    )
    return DraftingConfig(index, SIGN_OFF)


PROMISE = output(f"We will refund you today.\n\n{SIGN_OFF}")
GOOD = output(f"Hello, thank you for writing.\n\n{SIGN_OFF}")


def blocked(detail: str = "implies an outcome") -> JudgeOutput:
    issue = JudgeIssue(rule_id="G-3", claim="on its way", detail=detail)
    return JudgeOutput(grounded=False, issues=[issue], confidence=0.6)


async def test_a_grounded_first_draft_is_stored_with_its_verdict(config: DraftingConfig) -> None:
    client = ScriptedDrafter([GOOD])
    stage = await draft_ticket(client, config, ticket(), found())
    assert stage.draft is not None and stage.kind is DraftKind.LLM and stage.attempt == 1
    assert stage.grounding is not None and stage.grounding.passed
    assert stage.failed_attempts == []
    assert [c.purpose for c in stage.calls] == ["embed", "draft", "judge"]
    assert len(client.requests) == 1 and client.judge_requests[0].reply_text == GOOD.reply_text


async def test_a_rule_failure_is_retried_once_with_the_reasons(config: DraftingConfig) -> None:
    client = ScriptedDrafter([PROMISE, GOOD])
    stage = await draft_ticket(client, config, ticket(), found())
    assert stage.draft is not None and stage.kind is DraftKind.LLM and stage.attempt == 2
    assert client.requests[0].failures == []
    [reason, *rest] = client.requests[1].failures
    assert reason.startswith("G-3:") and "promises" in reason
    assert all(line.startswith("G-") for line in rest)
    [failed] = stage.failed_attempts
    assert (failed.attempt, failed.outcome) == (1, "grounding_failed")
    assert "G-3" in failed.rule_ids and failed.reasons == client.requests[1].failures
    # the failed draft was not judged (the rules are the authority); the second one was
    assert [c.purpose for c in stage.calls] == ["embed", "draft", "draft", "judge"]


async def test_a_judge_block_is_retried_with_the_judges_reason(config: DraftingConfig) -> None:
    accepted = JudgeOutput(grounded=True, confidence=0.8)
    client = ScriptedDrafter([GOOD, GOOD], judgements=[blocked("not in the order"), accepted])
    stage = await draft_ticket(client, config, ticket(), found())
    assert stage.draft is not None and stage.attempt == 2
    assert client.requests[1].failures == ["G-3: not in the order ('on its way')"]
    assert stage.failed_attempts[0].judge_confidence == 0.6
    assert [c.purpose for c in stage.calls].count("judge") == 2


async def test_two_failures_use_the_fallback_template(config: DraftingConfig) -> None:
    client = ScriptedDrafter([PROMISE, PROMISE])
    stage = await draft_ticket(client, config, ticket(), found())
    assert stage.escalation_reason is None and stage.draft is not None
    assert stage.kind is DraftKind.FALLBACK_TEMPLATE and stage.attempt == 3
    assert stage.draft.model == FALLBACK_MODEL and stage.draft.confidence == 0.0
    assert stage.draft.reply_text == config.grounding.fallback_text(SIGN_OFF)
    assert stage.draft.reply_text.endswith(SIGN_OFF)
    assert stage.grounding is not None and stage.grounding.passed
    assert [f.attempt for f in stage.failed_attempts] == [1, 2]
    assert len(client.requests) == 2  # asked twice, never a third time


async def test_the_fallback_keeps_what_the_draft_was_made_from(config: DraftingConfig) -> None:
    from uuid import uuid4

    snapshot = uuid4()
    stage = await draft_ticket(
        ScriptedDrafter([PROMISE, PROMISE]), config, ticket(), found(), snapshot_id=snapshot
    )
    assert stage.draft is not None
    assert stage.draft.order_snapshot_id == snapshot
    assert stage.draft.policy_version == config.index.version
    assert stage.passage_ids and "RET-1.1" in stage.passage_ids


async def test_an_invalid_answer_after_a_grounding_failure_falls_back(
    config: DraftingConfig,
) -> None:
    client = ScriptedDrafter([PROMISE, LLMInvalidOutputError("confidence: out of range")])
    stage = await draft_ticket(client, config, ticket(), found())
    assert stage.kind is DraftKind.FALLBACK_TEMPLATE and stage.draft is not None
    assert [f.outcome for f in stage.failed_attempts] == ["grounding_failed", "invalid_output"]


async def test_a_grounding_failure_then_a_grounded_draft_after_an_invalid_one(
    config: DraftingConfig,
) -> None:
    client = ScriptedDrafter([LLMInvalidOutputError("x"), PROMISE])
    stage = await draft_ticket(client, config, ticket(), found())
    assert stage.kind is DraftKind.FALLBACK_TEMPLATE
    assert [f.outcome for f in stage.failed_attempts] == ["invalid_output", "grounding_failed"]


async def test_two_invalid_answers_still_escalate(config: DraftingConfig) -> None:
    client = ScriptedDrafter([LLMInvalidOutputError("a"), LLMInvalidOutputError("b")])
    stage = await draft_ticket(client, config, ticket(), found())
    assert stage.draft is None and stage.escalation_reason == INVALID_OUTPUT_REASON


async def test_a_model_failure_on_the_retry_escalates(config: DraftingConfig) -> None:
    client = ScriptedDrafter([PROMISE, LLMRequestError(401)])
    stage = await draft_ticket(client, config, ticket(), found())
    assert stage.draft is None and stage.escalation_reason == "llm_error:LLMRequestError"


async def test_a_judge_that_cannot_answer_escalates_without_retrying(
    config: DraftingConfig,
) -> None:
    client = ScriptedDrafter([GOOD, GOOD], judgements=[LLMRequestError(500)])
    stage = await draft_ticket(client, config, ticket(), found())
    assert stage.draft is None and stage.escalation_reason == GROUNDING_UNAVAILABLE_REASON
    assert len(client.requests) == 1
    assert [c.purpose for c in stage.calls] == ["embed", "draft"]


async def test_a_template_that_fails_the_rules_escalates_instead_of_being_used(
    config: DraftingConfig,
) -> None:
    bad = default_config().model_copy(
        update={"fallback_template": "We will refund you today.\n\n{sign_off}"}
    )
    broken = dataclasses.replace(config, grounding=bad)
    stage = await draft_ticket(ScriptedDrafter([PROMISE, PROMISE]), broken, ticket(), found())
    assert stage.draft is None and stage.escalation_reason == FALLBACK_INVALID_REASON


async def test_the_committed_template_passes_the_rules_for_any_ticket(
    config: DraftingConfig,
) -> None:
    from services.api.context.lookup import ContextLookup, LookupResult

    for lookup in (found(), ContextLookup(LookupResult.NO_ORDER)):
        stage = await draft_ticket(ScriptedDrafter([PROMISE, PROMISE]), config, ticket(), lookup)
        assert stage.kind is DraftKind.FALLBACK_TEMPLATE
