"""The committed datasets pass every check, and each check fails when it should."""

from collections import Counter
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from evals import corpus
from evals.schema import BadDraft, LabeledTicket, Outcome
from services.api.models.enums import Intent, Urgency


@pytest.fixture(scope="module")
def tickets() -> list[LabeledTicket]:
    return corpus.load_labeled()


@pytest.fixture(scope="module")
def drafts() -> list[BadDraft]:
    return corpus.load_bad_drafts()


@pytest.fixture(scope="module")
def recorded() -> Any:
    return corpus.load_recorded()


def test_committed_datasets_have_no_problems(tickets: Any, drafts: Any, recorded: Any) -> None:
    assert corpus.validate_datasets(tickets, drafts, recorded) == []


def test_labeled_set_size_and_spread(tickets: list[LabeledTicket]) -> None:
    assert 60 <= len(tickets) <= 100
    assert {t.intent for t in tickets} == set(Intent)
    assert {t.urgency for t in tickets} == set(Urgency)
    assert {t.outcome for t in tickets} == set(Outcome)


def test_every_policy_id_and_every_edge_case_is_covered(tickets: list[LabeledTicket]) -> None:
    covered = {pid for t in tickets for pid in t.policy_ids}
    assert covered >= corpus.policy_ids_in_policies_md()
    assert {t.scenario_case for t in tickets if t.scenario_case} == set(range(1, 32))


def test_bad_drafts_cover_every_rule(drafts: list[BadDraft]) -> None:
    counts = Counter(rule for d in drafts for rule in d.expected_rules)
    assert all(counts[f"G-{n}"] >= 3 for n in range(1, 11))
    assert len(drafts) >= 30


def test_placeholders_resolve_to_the_seeded_order() -> None:
    assert corpus.resolve("order {order_id}", 3) == f"order {corpus.scenario(3).order_id}"
    assert corpus.placeholders(18) == {"order_id": "RD-99999"}
    assert corpus.placeholders(None) == {}
    assert "tracking" in corpus.placeholders(3)
    assert "refund_amount" in corpus.placeholders(14)
    assert corpus.order_json_for_case(18) is None


def test_ticket_ids_are_unique_and_recorded_for_each(tickets: Any, recorded: Any) -> None:
    assert len({t.id for t in tickets}) == len(tickets)
    assert {r.ticket_id for r in recorded} == {t.id for t in tickets}


Mutate = Callable[[list[LabeledTicket], list[BadDraft], list[Any]], None]


def _problems(tickets: Any, drafts: Any, recorded: Any, **kw: Any) -> list[str]:
    return corpus.validate_datasets(list(tickets), list(drafts), list(recorded), **kw)


def test_unknown_policy_id_in_a_label_is_reported(tickets: Any, drafts: Any, recorded: Any) -> None:
    bad = [t.model_copy(update={"policy_ids": [*t.policy_ids, "RET-9.9"]}) for t in tickets[:1]]
    problems = _problems(bad + tickets[1:], drafts, recorded)
    assert any("RET-9.9" in p and "not in policies.md" in p for p in problems)


def test_policy_id_nobody_references_is_reported(tickets: Any, drafts: Any, recorded: Any) -> None:
    known = corpus.policy_ids_in_policies_md() | {"XYZ-1.1"}
    problems = _problems(tickets, drafts, recorded, known_policies=known)
    assert any("XYZ-1.1" in p and "not referenced" in p for p in problems)


def test_missing_edge_case_is_reported(tickets: Any, drafts: Any, recorded: Any) -> None:
    kept = [t for t in tickets if t.scenario_case != 31]
    gone = {t.id for t in tickets} - {t.id for t in kept}
    problems = _problems(kept, drafts, [r for r in recorded if r.ticket_id not in gone])
    assert any("case 31 has no labeled ticket" in p for p in problems)


def test_wrong_customer_for_a_case_is_reported(tickets: Any, drafts: Any, recorded: Any) -> None:
    changed = [
        tickets[0].model_copy(update={"customer_email": "someone@example.com"}),
        *tickets[1:],
    ]
    assert any("is not case 1's customer" in p for p in _problems(changed, drafts, recorded))


def test_too_few_tickets_is_reported(tickets: Any, drafts: Any, recorded: Any) -> None:
    few = tickets[:10]
    problems = _problems(few, drafts, [r for r in recorded if r.ticket_id in {t.id for t in few}])
    assert any("the spec asks for 60-100" in p for p in problems)


def test_missing_and_extra_recorded_predictions_are_reported(
    tickets: Any, drafts: Any, recorded: Any
) -> None:
    problems = _problems(tickets, drafts, recorded[1:])
    assert any(f"no recorded prediction for {tickets[0].id}" in p for p in problems)
    ghost = recorded[0].model_copy(update={"ticket_id": "T999"})
    assert any("unknown ticket T999" in p for p in _problems(tickets, drafts, [*recorded, ghost]))
    assert any(
        "duplicate recorded" in p for p in _problems(tickets, drafts, [*recorded, recorded[0]])
    )


def _draft(drafts: list[BadDraft], rule: str) -> BadDraft:
    return next(d for d in drafts if rule in d.expected_rules)


@pytest.mark.parametrize(
    "rule, update, expected",
    [
        ("G-1", {"reply_text": "Hello,\n\nno sign-off here"}, "should end with the sign-off"),
        (
            "G-8",
            {"reply_text": "Hello.\n\nKaveri Home Goods Support"},
            "ends with the exact sign-off",
        ),
        ("G-1", {"reply_text": "Sorry, sorry.\n\nKaveri Home Goods Support"}, "not labeled G-9"),
        ("G-9", {"reply_text": "Sorry.\n\nKaveri Home Goods Support"}, "fewer than two apologies"),
        ("G-10", {"reply_text": "Short.\n\nKaveri Home Goods Support"}, "labeled G-10 but only"),
        (
            "G-1",
            {"reply_text": ("long " * 400) + "\n\nKaveri Home Goods Support"},
            "but not labeled G-10",
        ),
        (
            "G-1",
            {"reply_text": "We will refund you.\n\nKaveri Home Goods Support"},
            "not labeled G-3",
        ),
        ("G-3", {"reply_text": "Fine.\n\nKaveri Home Goods Support"}, "no commitment phrase"),
        (
            "G-1",
            {"reply_text": "Order {nope}\n\nKaveri Home Goods Support"},
            "unresolved placeholder",
        ),
    ],
)
def test_bad_draft_hygiene_checks(
    tickets: Any, drafts: Any, recorded: Any, rule: str, update: dict[str, str], expected: str
) -> None:
    target = _draft(drafts, rule)
    changed = [d.model_copy(update=update) if d.id == target.id else d for d in drafts]
    assert any(expected in p for p in _problems(tickets, changed, recorded))


def test_each_rule_needs_enough_drafts(tickets: Any, drafts: Any, recorded: Any) -> None:
    fewer = [d for d in drafts if "G-7" not in d.expected_rules]
    assert any("rule G-7 has 0 bad drafts" in p for p in _problems(tickets, fewer, recorded))


def test_missing_adversarial_tag_and_intent_are_reported(
    tickets: Any, drafts: Any, recorded: Any
) -> None:
    stripped = [
        t.model_copy(update={"tags": [x for x in t.tags if x != "delimiter_break"]})
        for t in tickets
    ]
    assert any("delimiter_break" in p for p in _problems(stripped, drafts, recorded))
    no_warranty = [t for t in tickets if t.intent is not Intent.WARRANTY]
    keep = {t.id for t in no_warranty}
    problems = _problems(no_warranty, drafts, [r for r in recorded if r.ticket_id in keep])
    assert any("intent warranty has 0 tickets" in p for p in problems)


def test_ticket_schema_rules() -> None:
    base: dict[str, Any] = {
        "id": "T001",
        "customer_email": "a@example.com",
        "body": "x",
        "intent": "refund",
        "urgency": "low",
        "sentiment": "neutral",
        "outcome": "draft",
    }
    assert LabeledTicket.model_validate(base)
    with pytest.raises(ValidationError, match="exactly when"):
        LabeledTicket.model_validate({**base, "escalation_reasons": ["abusive"]})
    with pytest.raises(ValidationError, match="exactly when"):
        LabeledTicket.model_validate({**base, "outcome": "no_draft_escalation"})
    with pytest.raises(ValidationError, match="outside_policy"):
        LabeledTicket.model_validate(
            {**base, "outcome": "suggestion_only", "escalation_reasons": ["abusive"]}
        )
    with pytest.raises(ValidationError, match="high_value"):
        LabeledTicket.model_validate({**base, "flags": ["high_value"]})
    with pytest.raises(ValidationError, match="lower-cased"):
        LabeledTicket.model_validate({**base, "customer_email": "A@Example.com"})
    with pytest.raises(ValidationError):
        LabeledTicket.model_validate({**base, "policy_ids": ["ret-1.1"]})
    with pytest.raises(ValidationError):
        LabeledTicket.model_validate({**base, "surprise": 1})


def test_load_names_the_line_that_fails(tmp_path: Any) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"id": "T001"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="bad.jsonl line 1"):
        corpus.load_labeled(path)
