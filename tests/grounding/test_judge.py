"""The judge and the combined check, over a scripted judge model."""

from typing import Any

import pytest

from services.api.grounding.check import check_grounding
from services.api.grounding.judge import judge_reply
from services.api.grounding.result import GroundingResult
from services.api.grounding.validator import GroundingContext
from services.api.llm.client import JudgeRequest, LLMResult, Passage
from services.api.llm.errors import LLMRequestError, LLMTimeoutError
from services.api.llm.fake import FakeLLMClient
from services.api.models.judge import JudgeIssue, JudgeOutput
from tests.grounding.conftest import order_cite, reply

pytestmark = pytest.mark.anyio

GOOD = reply("Your order RD-10001 was delivered.")
CITES = [order_cite("order_id")]
PASSAGES = [Passage(policy_id="RET-1.1", text="Items may be returned within 7 days.")]


class ScriptedJudge(FakeLLMClient):
    def __init__(self, item: Any) -> None:
        super().__init__()
        self.item = item
        self.requests: list[JudgeRequest] = []

    async def judge(self, request: JudgeRequest) -> LLMResult[JudgeOutput]:
        self.requests.append(request)
        if isinstance(self.item, Exception):
            raise self.item
        return LLMResult(self.item, self._record("judge", "judge"))


def grounded(confidence: float = 0.9) -> JudgeOutput:
    return JudgeOutput(grounded=True, confidence=confidence)


def blocked() -> JudgeOutput:
    issue = JudgeIssue(rule_id="G-3", claim="is on its way", detail="implies an outcome")
    return JudgeOutput(grounded=False, issues=[issue], confidence=0.7)


async def test_a_clean_draft_the_judge_accepts_passes(ctx: GroundingContext) -> None:
    judge = ScriptedJudge(grounded(0.9))
    result, calls = await check_grounding(judge, GOOD, CITES, ctx, PASSAGES)
    assert result.passed and result.deterministic == [] and result.judge_error is None
    assert result.judge is not None and result.judge.confidence == 0.9
    assert [c.purpose for c in calls] == ["judge"]


async def test_the_judge_can_block_what_the_validator_passed(ctx: GroundingContext) -> None:
    result, calls = await check_grounding(ScriptedJudge(blocked()), GOOD, CITES, ctx, PASSAGES)
    assert not result.passed and result.deterministic == []
    assert result.rule_ids == {"G-3"}
    assert result.reasons() == ["G-3: implies an outcome ('is on its way')"]
    assert len(calls) == 1


async def test_the_judge_is_not_asked_and_cannot_rescue_a_validator_failure(
    ctx: GroundingContext,
) -> None:
    judge = ScriptedJudge(grounded())
    bad = reply("We will refund you today.")
    result, calls = await check_grounding(judge, bad, [], ctx, PASSAGES)
    assert not result.passed and "G-3" in result.rule_ids
    assert result.judge is None and calls == [] and judge.requests == []


async def test_a_judge_that_cannot_answer_blocks_the_draft(ctx: GroundingContext) -> None:
    for error, reason in (
        (LLMTimeoutError("slow"), "llm_error:LLMTimeoutError"),
        (LLMRequestError(401), "llm_error:LLMRequestError"),
    ):
        result, calls = await check_grounding(ScriptedJudge(error), GOOD, CITES, ctx, PASSAGES)
        assert not result.passed and result.judge is None and calls == []
        assert result.judge_error == reason and result.reasons() == []


async def test_the_judge_sees_the_order_without_the_customers_email(ctx: GroundingContext) -> None:
    judge = ScriptedJudge(grounded())
    await check_grounding(judge, GOOD, CITES, ctx, PASSAGES)
    sent = judge.requests[0]
    assert sent.reply_text == GOOD and sent.citations == CITES and sent.passages == PASSAGES
    assert sent.order is not None and "customer_email" not in sent.order
    assert sent.order["order_id"] == "RD-10001"


async def test_with_no_order_the_judge_gets_none(ctx: GroundingContext) -> None:
    from dataclasses import replace

    judge = ScriptedJudge(grounded())
    await check_grounding(judge, reply("Hello."), [], replace(ctx, order=None), PASSAGES)
    assert judge.requests[0].order is None


async def test_judge_reply_returns_the_output_and_the_call() -> None:
    stage = await judge_reply(ScriptedJudge(grounded(0.6)), GOOD, CITES, None, PASSAGES)
    assert stage.output is not None and stage.output.confidence == 0.6
    assert stage.call is not None and stage.error is None


def test_result_reasons_combine_both_sources_and_serialise() -> None:
    from services.api.grounding.models import RuleFailure

    result = GroundingResult(
        passed=False,
        deterministic=[RuleFailure(rule_id="G-9", span="sorry", detail="two apologies")],
        judge=JudgeOutput(
            grounded=False,
            issues=[JudgeIssue(claim="it is fine", detail="no source")],
            confidence=0.5,
        ),
    )
    assert result.reasons() == ["G-9: two apologies ('sorry')", "judge: no source ('it is fine')"]
    assert result.rule_ids == {"G-9"}
    payload = result.audit_payload()
    assert payload["passed"] is False and payload["judge"]["confidence"] == 0.5
    assert GroundingResult.model_validate(payload) == result
