import pytest

from services.api.escalation.pre_lookup import decide
from services.api.escalation.safety import scan_safety
from services.api.models.enums import Intent, Sentiment, Urgency
from services.api.models.ticket import Classification


@pytest.mark.parametrize(
    "body",
    [
        "The mixer grinder started smoking from the motor and smells of burning.",
        "It gave off sparks and a burning smell when I switched it on.",
        "The kettle caught fire on the counter.",
        "I got an electric shock when I touched the body of the heater.",
        "The pressure cooker exploded in the kitchen.",
        "My son was injured and we went to the hospital.",
        "The plug melted and there was a short circuit.",
        "There is a gas leak from the stove connector.",
        "The toaster is s m0king and the wire is sp@rking",
    ],
)
def test_a_hazard_is_found(body: str) -> None:
    assert scan_safety(None, body)


@pytest.mark.parametrize(
    "body",
    [
        "Where is my order? It was due on Monday.",
        "I would like the smoke grey colour instead of black.",
        "Is the glass fireproof?",
        "I was shocked at how late the parcel is.",
        "Please send the invoice. This is not an emergency.",
    ],
)
def test_ordinary_messages_are_not_flagged(body: str) -> None:
    assert scan_safety("Question", body) == []


def test_the_subject_counts() -> None:
    assert scan_safety("Grinder is smoking", "Please help.")


def test_a_confident_warranty_ticket_about_smoke_escalates_without_a_flag() -> None:
    classification = Classification(
        intent=Intent.WARRANTY,
        urgency=Urgency.CRITICAL,
        confidence=0.95,
        sentiment=Sentiment.NEGATIVE,
        flags=[],
    )
    decision = decide(classification, "Grinder smoking", "It smells of burning. My child was near.")
    assert decision.escalate and decision.reasons == ["safety"]
    assert decision.safety_signals
