import pytest

from services.api.escalation.keyword_backstop import scan
from services.api.models.enums import Intent, Urgency
from services.api.pipeline.baseline_classifier import PRIORITY, SIGNALS, classify


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("The planter arrived broken and cracked.", Intent.DAMAGED_ITEM),
        ("Mera planter toot kar aaya", Intent.DAMAGED_ITEM),
        ("Please cancel my order today.", Intent.CANCELLATION),
        ("I want to change my delivery address.", Intent.ADDRESS_CHANGE),
        ("Could the parcel go to my office address instead?", Intent.ADDRESS_CHANGE),
        ("Money was debited but no order confirmation.", Intent.PAYMENT),
        ("Where is my refund? I want my money back.", Intent.REFUND),
        ("How do I return this?", Intent.RETURN),
        ("The kettle stopped working. Is it under warranty?", Intent.WARRANTY),
        ("Where is my order? Tracking has not moved.", Intent.ORDER_STATUS),
        ("Do you sell brass diyas? Are they in stock?", Intent.PRODUCT_QUESTION),
        ("Hello, can someone call me?", Intent.OTHER),
        ("", Intent.OTHER),
    ],
)
def test_intent_from_keywords(text: str, intent: Intent) -> None:
    assert classify(None, text).intent is intent


def test_the_word_order_alone_is_not_evidence() -> None:
    result = classify(None, "I have a question about my order.")
    assert result.intent is Intent.OTHER
    assert result.scores == {}


def test_subject_is_read_and_disguised_words_are_still_found() -> None:
    assert classify("CANCEL", "please").intent is Intent.CANCELLATION
    assert classify(None, "my p@ckage is br0ken").intent is Intent.DAMAGED_ITEM


def test_highest_score_wins_and_ties_follow_priority() -> None:
    mixed = classify(None, "Where is my order? If it is late can I return it?")
    assert mixed.scores["order_status"] > mixed.scores["return"]
    assert mixed.intent is Intent.ORDER_STATUS
    tie = classify(None, "refund or return")
    assert tie.scores["refund"] == tie.scores["return"]
    assert tie.intent is Intent.REFUND


def test_priority_covers_every_scored_intent_except_other() -> None:
    assert {s.intent for s in SIGNALS} <= set(PRIORITY)
    assert Intent.OTHER not in PRIORITY


@pytest.mark.parametrize(
    ("text", "urgency"),
    [
        ("The heater sparked and there was a burning smell.", Urgency.CRITICAL),
        ("Where is my order?", Urgency.NORMAL),
        ("The mug arrived chipped.", Urgency.HIGH),
        ("Do you sell gift wrapping?", Urgency.LOW),
        ("hello", Urgency.LOW),
        ("This is my fourth message about the delay.", Urgency.HIGH),
        (
            "URGENT!!! Where is my order",
            Urgency.NORMAL,
        ),  # rubric rule 1: bare urgency is not urgent
    ],
)
def test_urgency_follows_the_rubric(text: str, urgency: Urgency) -> None:
    assert classify(None, text).urgency is urgency


def test_a_legal_backstop_match_makes_the_ticket_critical() -> None:
    text = "I have told my bank to do a chargeback."
    assert classify(None, text).urgency is not Urgency.CRITICAL
    assert classify(None, text, scan(None, text)).urgency is Urgency.CRITICAL
    abusive = "You idiots."
    assert classify(None, abusive, scan(None, abusive)).urgency is not Urgency.CRITICAL
