from evals import corpus
from evals.baseline import BaselinePipeline
from evals.metrics import classification_metrics, escalation_metrics
from evals.schema import LabeledTicket, Outcome


def _ticket(**overrides: object) -> LabeledTicket:
    base = corpus.load_labeled()[0].model_dump()
    return LabeledTicket.model_validate(base | overrides)


def test_the_baseline_does_not_gate_the_run() -> None:
    assert BaselinePipeline.gated is False
    assert BaselinePipeline.name == "keyword-baseline"


def test_backstop_match_gives_a_no_draft_escalation_with_its_reason() -> None:
    prediction = BaselinePipeline().predict(
        _ticket(body="I will file a case in the consumer court.", scenario_case=None)
    )
    assert prediction.outcome is Outcome.NO_DRAFT_ESCALATION
    assert prediction.escalation_reasons == ["legal_threat"]
    assert prediction.urgency == "critical"


def test_unrecognised_intent_escalates_as_intent_other() -> None:
    prediction = BaselinePipeline().predict(
        _ticket(subject="Hi", body="Can someone call me?", scenario_case=None)
    )
    assert prediction.intent.value == "other"
    assert prediction.escalation_reasons == ["intent_other"]


def test_a_plain_request_is_a_draft_and_the_placeholders_are_resolved() -> None:
    ticket = _ticket(subject="Order status", body="Where is my order {order_id}?")
    prediction = BaselinePipeline().predict(ticket)
    assert prediction.outcome is Outcome.DRAFT
    assert prediction.escalation_reasons == []


def test_baseline_never_predicts_suggestion_only() -> None:
    tickets = corpus.load_labeled()
    pipeline = BaselinePipeline()
    assert all(pipeline.predict(t).outcome is not Outcome.SUGGESTION_ONLY for t in tickets)


def test_the_baseline_is_weaker_than_a_model_must_be_on_escalation() -> None:
    tickets = corpus.load_labeled()
    pipeline = BaselinePipeline()
    predictions = {t.id: pipeline.predict(t) for t in tickets}
    assert classification_metrics(tickets, predictions).accuracy.rate is not None
    recall = escalation_metrics(tickets, predictions).recall.rate
    assert recall is not None and recall < 0.95  # why a model is worth paying for
