"""The live system under test, run against a recorded client so no model is called."""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from evals import corpus, runner
from evals.live import CostLimitExceeded, LivePipeline, percentile
from evals.schema import LabeledTicket, Outcome
from services.api.llm.client import CallRecord, DraftRequest, LLMResult
from services.api.llm.errors import LLMRequestError
from services.api.llm.fake import FakeLLMClient
from services.api.models.draft import DraftOutput


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


def test_a_failing_drafter_escalates_the_ticket_and_the_report_counts_it() -> None:
    pipeline = live()
    try:
        tickets = [t for t in corpus.load_labeled() if t.outcome is Outcome.DRAFT][:3]
        outcomes = [pipeline.predict(t).outcome for t in tickets]
        report = pipeline.report()
    finally:
        pipeline.close()
    assert outcomes == [Outcome.NO_DRAFT_ESCALATION] * 3
    assert report.drafts_attempted == 3
    assert report.draft_escalated.successes == 3
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
