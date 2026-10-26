import pytest

from evals.metrics import (
    Prediction,
    classification_metrics,
    escalation_metrics,
    grounding_metrics,
    rate,
)
from evals.schema import BadDraft, LabeledTicket, Outcome
from services.api.models.enums import Intent


def test_wilson_interval_matches_known_values() -> None:
    half = rate(5, 10)
    assert half.rate == 0.5
    assert half.low == pytest.approx(0.2366, abs=1e-3)
    assert half.high == pytest.approx(0.7634, abs=1e-3)
    perfect = rate(10, 10)
    assert perfect.rate == 1.0
    assert perfect.low == pytest.approx(0.7225, abs=1e-3)
    assert perfect.high == pytest.approx(1.0)
    none = rate(0, 10)
    assert none.low == 0.0
    assert none.high == pytest.approx(0.2775, abs=1e-3)


def test_wilson_interval_narrows_with_more_data() -> None:
    small, large = rate(9, 10), rate(900, 1000)
    assert small.low is not None and small.high is not None
    assert large.low is not None and large.high is not None
    assert (large.high - large.low) < (small.high - small.low)


def test_empty_denominator_is_undefined_not_zero() -> None:
    empty = rate(0, 0)
    assert empty.rate is None and empty.low is None and empty.high is None


@pytest.mark.parametrize("successes, total", [(-1, 5), (6, 5)])
def test_impossible_counts_are_rejected(successes: int, total: int) -> None:
    with pytest.raises(ValueError):
        rate(successes, total)


def _ticket(n: int, intent: str, outcome: str, urgency: str = "normal") -> LabeledTicket:
    reasons = {
        "draft": [],
        "no_draft_escalation": ["abusive"],
        "suggestion_only": ["outside_policy"],
    }
    return LabeledTicket(
        id=f"T{n:03d}",
        customer_email="a@example.com",
        body="x",
        intent=intent,
        urgency=urgency,
        sentiment="neutral",
        outcome=outcome,
        escalation_reasons=reasons[outcome],
    )


def _pred(intent: str, outcome: str, urgency: str = "normal") -> Prediction:
    return Prediction(intent=intent, urgency=urgency, outcome=outcome)  # type: ignore[arg-type]


def test_per_intent_precision_and_recall_by_hand() -> None:
    tickets = [
        _ticket(1, "refund", "draft"),
        _ticket(2, "refund", "draft"),
        _ticket(3, "refund", "draft"),
        _ticket(4, "return", "draft"),
    ]
    predictions = {
        "T001": _pred("refund", "draft"),
        "T002": _pred("refund", "draft"),
        "T003": _pred("return", "draft"),  # a refund called a return
        "T004": _pred("refund", "draft"),  # a return called a refund
    }
    metrics = classification_metrics(tickets, predictions)
    by_intent = {m.intent: m for m in metrics.per_intent}
    refund, ret = by_intent[Intent.REFUND], by_intent[Intent.RETURN]
    assert (refund.precision.successes, refund.precision.total) == (2, 3)
    assert (refund.recall.successes, refund.recall.total) == (2, 3)
    assert (ret.precision.successes, ret.precision.total) == (0, 1)
    assert (ret.recall.successes, ret.recall.total) == (0, 1)
    assert by_intent[Intent.PAYMENT].precision.rate is None  # never predicted, never labeled
    assert (metrics.accuracy.successes, metrics.accuracy.total) == (2, 4)
    assert len(metrics.per_intent) == len(Intent)


def test_urgency_accuracy() -> None:
    tickets = [_ticket(1, "refund", "draft", "high"), _ticket(2, "refund", "draft", "low")]
    predictions = {
        "T001": _pred("refund", "draft", "high"),
        "T002": _pred("refund", "draft", "normal"),
    }
    assert classification_metrics(tickets, predictions).urgency_accuracy.successes == 1


def test_escalation_counts_suggestion_only_as_escalation() -> None:
    tickets = [
        _ticket(1, "refund", "no_draft_escalation"),
        _ticket(2, "refund", "suggestion_only"),
        _ticket(3, "refund", "suggestion_only"),
        _ticket(4, "refund", "draft"),
        _ticket(5, "refund", "draft"),
    ]
    predictions = {
        "T001": _pred("refund", "no_draft_escalation"),
        "T002": _pred("refund", "no_draft_escalation"),  # escalated, in a different form
        "T003": _pred("refund", "draft"),  # missed
        "T004": _pred("refund", "suggestion_only"),  # false escalation
        "T005": _pred("refund", "draft"),
    }
    m = escalation_metrics(tickets, predictions)
    assert (m.recall.successes, m.recall.total) == (2, 3)
    assert (m.precision.successes, m.precision.total) == (2, 3)
    assert (m.no_draft_recall.successes, m.no_draft_recall.total) == (1, 1)
    assert (m.outcome_accuracy.successes, m.outcome_accuracy.total) == (2, 5)
    assert m.missed == ["T003"]
    assert m.false_escalations == ["T004"]


def _bad(n: int, rules: list[str]) -> BadDraft:
    return BadDraft.model_validate(
        {
            "id": f"B{n:03d}",
            "order_case": None,
            "expected_rules": rules,
            "description": "d",
            "reply_text": "r",
        }
    )


def test_grounding_metrics_report_misses_and_wrong_rules() -> None:
    drafts = [_bad(1, ["G-1"]), _bad(2, ["G-3"]), _bad(3, ["G-8"])]
    verdicts = {"B001": {"G-1"}, "B002": {"G-5"}, "B003": set()}

    def validator(draft: BadDraft, order: dict[str, object] | None) -> set[str]:
        return verdicts[draft.id]

    m = grounding_metrics(drafts, validator, lambda d: None)  # type: ignore[arg-type]
    assert (m.caught.successes, m.caught.total) == (2, 3)  # B003 slipped through
    assert (m.rule_match.successes, m.rule_match.total) == (1, 3)  # B002 caught for the wrong rule
    assert m.missed == ["B003"]


def test_outcome_enum_knows_which_values_are_escalations() -> None:
    assert not Outcome.DRAFT.is_escalation
    assert Outcome.SUGGESTION_ONLY.is_escalation
    assert Outcome.NO_DRAFT_ESCALATION.is_escalation
