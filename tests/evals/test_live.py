"""The live system under test, run against a recorded client so no model is called."""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from evals import corpus, runner
from evals.live import (
    CostLimitExceeded,
    LivePipeline,
    PacedClient,
    ProviderUnavailable,
    draft_kind,
    is_provider_failure,
    percentile,
)
from evals.schema import LabeledTicket, Outcome
from services.api.llm.client import CallRecord, DraftRequest, LLMResult
from services.api.llm.errors import LLMRequestError
from services.api.llm.fake import FakeLLMClient
from services.api.models.draft import DraftOutput
from services.api.pipeline.draft import FALLBACK_MODEL, DraftStage


class Priced(FakeLLMClient):
    """Recorded classifications; every call costs Rs 1; the drafter is down."""

    def _record(self, purpose: Any, prompt_name: Any, tokens: int = 0) -> CallRecord:
        return (
            super()
            ._record(purpose, prompt_name, tokens)
            .model_copy(update={"cost_inr": Decimal(1)})
        )

    async def draft(self, request: DraftRequest) -> LLMResult[DraftOutput]:
        raise LLMRequestError(500, "down")


def live(limit: str = "1000") -> LivePipeline:
    client = Priced.from_jsonl(corpus.RECORDED_PATH)
    return LivePipeline(client, drafting_model="m", judge_model="j", max_cost_inr=Decimal(limit))


def by_outcome(outcome: Outcome) -> LabeledTicket:
    return next(t for t in corpus.load_labeled() if t.outcome is outcome)


def test_a_ticket_the_rules_escalate_is_never_drafted() -> None:
    pipeline = live()
    try:
        ticket = by_outcome(Outcome.NO_DRAFT_ESCALATION)
        prediction = pipeline.predict(ticket)
        [detail] = pipeline.details
    finally:
        pipeline.close()
    assert prediction.outcome is Outcome.NO_DRAFT_ESCALATION
    assert not detail.reached_drafting and detail.drafted is None
    assert pipeline.drafts == {}


def test_a_failing_drafter_escalates_the_ticket_and_the_report_counts_it() -> None:
    pipeline = live()
    try:
        tickets = [t for t in corpus.load_labeled() if t.outcome is Outcome.DRAFT][:2]
        outcomes = [pipeline.predict(t).outcome for t in tickets]
        report = pipeline.report()
    finally:
        pipeline.close()
    assert outcomes == [Outcome.NO_DRAFT_ESCALATION] * 2
    assert report.drafts_attempted == 2
    assert report.draft_escalated.successes == 2
    assert report.model_draft_any_try.successes == 0
    assert all(d.draft_escalation == "llm_error:LLMRequestError" for d in report.tickets)


def test_cost_is_summed_per_ticket_with_the_policy_embedding() -> None:
    pipeline = live()
    try:
        pipeline.predict(by_outcome(Outcome.NO_DRAFT_ESCALATION))
        report = pipeline.report()
    finally:
        pipeline.close()
    assert report.setup_cost_inr == 1.0  # one embed call
    assert report.tickets[0].cost_inr == 1.0  # one classify call
    assert report.total_cost_inr == 2.0 and report.cost_per_ticket_inr == 2.0


def test_the_run_stops_when_spend_passes_the_limit() -> None:
    pipeline = live("1.5")
    try:
        with pytest.raises(CostLimitExceeded):
            pipeline.predict(by_outcome(Outcome.NO_DRAFT_ESCALATION))
    finally:
        pipeline.close()


def stage(model: str | None) -> DraftStage:
    draft = None if model is None else DraftOutput.model_construct(model=model)
    return DraftStage(draft=draft)


def test_the_fallback_template_is_not_counted_as_a_model_draft() -> None:
    assert draft_kind(stage("gemini-x")) == "llm"
    assert draft_kind(stage(FALLBACK_MODEL)) == "fallback_template"
    assert draft_kind(stage(None)) is None
    assert draft_kind(None) is None


def test_percentile() -> None:
    assert percentile([], 0.5) is None
    assert percentile([1, 2, 3, 4], 0.5) == 2
    assert percentile([1, 2, 3, 4], 0.95) == 4


def configure(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    env = {
        "LLM_PROVIDER": "gemini",
        "LLM_API_KEY": "k",
        "LLM_MODEL": "m",
        "JUDGE_PROVIDER": "openai",
        "JUDGE_API_KEY": "k",
        "JUDGE_MODEL": "j",
        "EMBED_PROVIDER": "openai",
    } | extra
    monkeypatch.chdir(Path(__file__).parent)  # no .env in sight
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_full_mode_refuses_without_a_live_provider(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(Path(__file__).parent)
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    assert runner.main(["--mode", "full", "--max-cost-inr", "50"]) == 2
    assert "no live model" in capsys.readouterr().err


def test_full_mode_refuses_when_prices_are_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(monkeypatch)
    assert runner.main(["--mode", "full", "--max-cost-inr", "50"]) == 2
    assert "could ever stop the run" in capsys.readouterr().err


def test_full_mode_refuses_without_a_cost_limit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    prices = {
        k: "10"
        for k in (
            "LLM_INPUT_PRICE_INR_PER_1M",
            "LLM_OUTPUT_PRICE_INR_PER_1M",
            "JUDGE_INPUT_PRICE_INR_PER_1M",
            "JUDGE_OUTPUT_PRICE_INR_PER_1M",
        )
    }
    configure(monkeypatch, **prices)
    assert runner.main(["--mode", "full"]) == 2
    assert "--max-cost-inr" in capsys.readouterr().err


def test_a_failed_call_is_a_provider_failure_and_a_bad_answer_is_not() -> None:
    assert is_provider_failure("llm_error:LLMRateLimitError")
    assert is_provider_failure("llm_error:CircuitOpenError")
    assert is_provider_failure("grounding_unavailable")
    assert not is_provider_failure("invalid_output")
    assert not is_provider_failure("high_value")
    assert not is_provider_failure(None)


def test_provider_failures_are_counted_apart_from_model_failures() -> None:
    pipeline = live()
    try:
        tickets = [t for t in corpus.load_labeled() if t.outcome is Outcome.DRAFT][:2]
        for t in tickets:
            pipeline.predict(t)
        report = pipeline.report()
    finally:
        pipeline.close()
    assert report.provider_failures == {"llm_error:LLMRequestError": 2}
    assert all(d.provider_error for d in report.tickets)


def test_the_run_stops_after_three_provider_failures_in_a_row() -> None:
    pipeline = live()
    try:
        tickets = [t for t in corpus.load_labeled() if t.outcome is Outcome.DRAFT][:3]
        with pytest.raises(ProviderUnavailable):
            for t in tickets:
                pipeline.predict(t)
    finally:
        pipeline.close()


def test_a_run_with_provider_failures_is_invalid_not_a_pass() -> None:
    pipeline = live()
    try:
        tickets = [t for t in corpus.load_labeled() if t.outcome is Outcome.DRAFT][:2]
        report = runner.run(
            mode="full",
            pipelines=[pipeline],
            model="m",
            temperature=0.0,
            run_id="t",
            on=corpus.EVAL_NOW.date(),
            tickets=tickets,
            live=pipeline.report,
            trial=True,
        )
    finally:
        pipeline.close()
    assert not report.passed
    assert any("not valid" in f for f in report.failures)


class Stopwatch:
    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 6))
        self.now += seconds


@pytest.mark.anyio
async def test_calls_are_spaced_by_the_rate() -> None:
    clock = Stopwatch()
    client = PacedClient(
        Priced.from_jsonl(corpus.RECORDED_PATH),
        30,  # one call every two seconds
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    for _ in range(3):
        await client.embed(["a"])
    assert clock.slept == [2.0, 2.0]  # the first call goes straight away


def test_a_pace_of_zero_is_refused() -> None:
    with pytest.raises(ValueError, match="above zero"):
        PacedClient(Priced(), 0)
