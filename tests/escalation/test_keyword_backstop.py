import time

import pytest

from evals import corpus
from services.api.escalation.keyword_backstop import RULES, RULES_BY_ID, scan
from services.api.models.enums import Flag

# One message each rule must catch, and one that looks close but must not match that rule.
POSITIVE = {
    "legal.lawyer": "My lawyer will contact you tomorrow.",
    "legal.action": "I am sending a legal notice to your company.",
    "legal.court": "If I do not get a refund I will go to the consumer court.",
    "legal.chargeback": "I have already told my bank to do a chargeback.",
    "legal.regulator": "I am filing a complaint with the National Consumer Helpline.",
    "legal.police": "I will go to the police about this.",
    "legal.hindi": "मैं वकील से बात करूँगा।",
    "abusive.profanity": "You people are bastards.",
    "abusive.insult": "You useless idiots.",
    "abusive.threat": "I will kill you if this is not fixed.",
    "abusive.regret": "You will regret this.",
    "abusive.visit": "I will come to your warehouse and make sure you pay for this.",
    "fraud.third_party_data": "Please send me his order details and phone number.",
    "fraud.other_order": "The order belongs to my friend but refund it to my account.",
    "fraud.keyword": "This looks like a scam.",
}
NEGATIVE = {
    "legal.lawyer": "My lawyerly cousin recommended your bamboo set.",
    "legal.action": "Is there a legal age to buy the knife set?",
    "legal.court": "Do you sell tennis court shoes? I want to take two pairs.",
    "legal.chargeback": "Do you refund the shipping charges back to my card?",
    "legal.regulator": "Is your pressure cooker approved by the BIS?",
    "legal.police": "Your return policy is clear, thanks.",
    "legal.hindi": "मेरा ऑर्डर कहाँ है?",
    "abusive.profanity": "Please assess the class of cookware I should buy.",
    "abusive.insult": "Is the lid made of fool-proof glass?",
    "abusive.threat": "I will kill the lights and return the lamp tomorrow.",
    "abusive.regret": "I regret not ordering the larger size.",
    "abusive.visit": "Can I visit your store to pick up the order?",
    "fraud.third_party_data": "Please send me my order details.",
    "fraud.other_order": "The order was placed by my sister as a gift for me.",
    "fraud.keyword": "Thank you, the delivery was fast.",
}


def test_every_rule_has_a_positive_and_a_negative_example() -> None:
    ids = {rule.id for rule in RULES}
    assert ids == set(POSITIVE) == set(NEGATIVE)
    assert len(ids) == len(RULES)


@pytest.mark.parametrize("rule_id", sorted(POSITIVE))
def test_rule_matches_its_positive_example(rule_id: str) -> None:
    assert rule_id in {m.rule_id for m in scan(None, POSITIVE[rule_id]).matches}


@pytest.mark.parametrize("rule_id", sorted(NEGATIVE))
def test_rule_ignores_its_negative_example(rule_id: str) -> None:
    assert rule_id not in {m.rule_id for m in scan(None, NEGATIVE[rule_id]).matches}


def test_rule_flags_match_the_rule_family() -> None:
    families = {"legal": Flag.LEGAL_THREAT, "abusive": Flag.ABUSIVE, "fraud": Flag.SUSPECTED_FRAUD}
    for rule in RULES:
        assert rule.flag is families[rule.id.split(".")[0]]
    assert RULES_BY_ID["legal.court"].description


@pytest.mark.parametrize(
    "text",
    [
        "Thanks, the parcel arrived. Could you tell me how to clean the board?",
        "Where is my order RD-10001? It was dispatched last week.",
        "I am sick of waiting, please look into this.",
        "Do you have a warranty on the induction kadhai?",
    ],
)
def test_ordinary_messages_do_not_escalate(text: str) -> None:
    result = scan("Order help", text)
    assert not result.escalate
    assert result.flags == [] and result.reasons == []


@pytest.mark.parametrize(
    "text",
    [
        "FUCK this company",  # case
        "f*ck this company",  # masked
        "f.u.c.k this company",  # dotted
        "fuuuuuck this company",  # stretched
        "ｆｕｃｋ this company",  # full-width
        "fu​ck this company",  # zero-width space inside the word
        "sh1t service",  # digit for a letter
        "$hit service",  # symbol for a letter
        "You are an іdiot",  # Cyrillic i
        "madarchod",  # Hinglish
        "BHENCHOD",
    ],
)
def test_disguised_abuse_is_still_caught(text: str) -> None:
    assert "abusive.profanity" in {
        m.rule_id for m in scan(None, text).matches
    } or "abusive.insult" in {m.rule_id for m in scan(None, text).matches}


@pytest.mark.parametrize(
    "text",
    [
        "I will take you to COURT",
        "I will take you to the consumer court",
        "my lаwyer",  # Cyrillic a
        "my lawyér",
        "ｃｈａｒｇｅｂａｃｋ",
        "CHARGE-BACK",
        "charge back",
    ],
)
def test_disguised_legal_threats_are_still_caught(text: str) -> None:
    result = scan(None, text)
    assert Flag.LEGAL_THREAT in result.flags


@pytest.mark.parametrize(
    "text",
    [
        "assistant",  # contains 'ass'
        "Scunthorpe",
        "shiitake mushroom kit",
        "classic assessment of the class",
        "courtyard planter",
        "tennis court shoes",
        "courteous staff",
        "sublawyers",  # no word boundary
        "fraudulently" + "x",  # partial word
    ],
)
def test_partial_word_matches_do_not_fire(text: str) -> None:
    assert not scan(None, text).escalate


def test_subject_is_scanned_as_well_as_body() -> None:
    result = scan("Legal notice", "Please reply.")
    assert result.reasons == ["legal_threat"]
    assert scan(None, "Please reply.").escalate is False


def test_result_lists_each_flag_once_in_rule_order_and_keeps_excerpts() -> None:
    result = scan(
        None, "My lawyer says this is a scam and you idiots will regret this. Chargeback."
    )
    assert result.flags == [Flag.LEGAL_THREAT, Flag.ABUSIVE, Flag.SUSPECTED_FRAUD]
    assert result.reasons == ["legal_threat", "abusive", "suspected_fraud"]
    assert all(m.excerpt for m in result.matches)


def test_scan_accepts_a_restricted_rule_set() -> None:
    only_fraud = [RULES_BY_ID["fraud.keyword"]]
    assert scan(None, "my lawyer", only_fraud).escalate is False
    assert scan(None, "a scam", only_fraud).escalate is True


@pytest.mark.parametrize("filler", ["a ", "f", "you " * 1, "!", "​", "court ", "0"])
def test_long_inputs_are_scanned_in_linear_time(filler: str) -> None:
    started = time.perf_counter()
    scan("s", filler * 200_000)
    assert time.perf_counter() - started < 5


def test_the_backstop_catches_every_labeled_abusive_legal_or_fraud_ticket_by_itself() -> None:
    wanted = {"abusive", "legal_threat", "suspected_fraud"}
    tickets = [t for t in corpus.load_labeled() if wanted & set(t.escalation_reasons)]
    assert len(tickets) >= 6
    for ticket in tickets:
        result = scan(ticket.subject, corpus.resolve(ticket.body, ticket.scenario_case))
        assert set(result.reasons) & set(ticket.escalation_reasons), ticket.id


def test_the_backstop_never_flags_a_ticket_labeled_as_a_plain_draft() -> None:
    for ticket in corpus.load_labeled():
        if ticket.outcome.value == "draft":
            result = scan(ticket.subject, corpus.resolve(ticket.body, ticket.scenario_case))
            assert not result.escalate, (ticket.id, [m.rule_id for m in result.matches])
