import asyncio

from evals import corpus, retrieval
from evals.schema import LabeledTicket, Outcome
from services.api.llm.fake import FakeLLMClient
from services.api.models.enums import Intent


def ticket(**overrides: object) -> LabeledTicket:
    fields: dict[str, object] = {
        "id": "T900",
        "customer_email": "a@example.com",
        "subject": "Return",
        "body": "I would like to return my order.",
        "intent": Intent.RETURN,
        "urgency": "normal",
        "sentiment": "neutral",
        "outcome": Outcome.DRAFT,
        "policy_ids": ["RET-1.1"],
    } | overrides
    return LabeledTicket.model_validate(fields)


def measure(*tickets: LabeledTicket) -> retrieval.RetrievalMetrics:
    return asyncio.run(retrieval.measure(list(tickets), FakeLLMClient()))


def test_a_passage_the_intent_requires_is_always_found() -> None:
    result = measure(ticket())
    assert result.tickets == 1 and result.tickets_complete.rate == 1.0
    assert result.passages_by_intent_only.rate == 1.0 and result.missed == []


def test_a_passage_neither_required_nor_close_is_a_miss() -> None:
    result = measure(ticket(policy_ids=["PAY-7.3"], body="return"))
    assert result.missed == ["T900"] and result.passages.rate == 0.0


def test_tone_passages_and_escalated_tickets_are_not_measured() -> None:
    escalated = ticket(
        id="T901",
        outcome=Outcome.NO_DRAFT_ESCALATION,
        escalation_reasons=["high_value"],
        policy_ids=["ESC-8.1"],
    )
    result = measure(ticket(policy_ids=["TON-9.1"]), escalated)
    assert result.tickets == 0 and result.passages.rate is None


def test_a_suggestion_only_ticket_gets_the_owner_review_passage() -> None:
    suggestion = ticket(
        outcome=Outcome.SUGGESTION_ONLY,
        escalation_reasons=["outside_policy"],
        policy_ids=["ESC-8.3"],
    )
    result = measure(suggestion)
    assert result.passages_by_intent_only.rate == 1.0


def test_the_committed_dataset_is_measured_and_every_intent_requirement_is_met() -> None:
    result = measure(*corpus.load_labeled())
    assert result.tickets > 0
    assert result.passages.rate is not None and result.passages_by_intent_only.rate is not None
    assert result.passages.rate >= result.passages_by_intent_only.rate
