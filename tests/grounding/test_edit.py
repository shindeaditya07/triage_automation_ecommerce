"""Checking an owner's edit: the warning, and the override that records why it was ignored."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from services.api.grounding.edit import (
    EditContext,
    EditOverride,
    OverrideError,
    check_override,
    validate_edit,
)
from tests.grounding.conftest import ORDER, POLICIES, SIGN_OFF, order_cite, policy_cite, reply

WHEN = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def edit_context() -> EditContext:
    return EditContext(
        draft_id=uuid4(),
        customer_email=ORDER["customer_email"],
        subject="Where is my bottle?",
        body="Order RD-10001.",
        order=ORDER,
        citations=[order_cite("order_id"), policy_cite("RET-1.1")],
    )


def check(text: str) -> object:
    return validate_edit(text, edit_context(), sign_off=SIGN_OFF, policy_texts=POLICIES)


def override(rules: list[str], reason: str = "The customer asked for this wording") -> EditOverride:
    return EditOverride(
        reason=reason, overridden_rules=rules, actor="owner@example.com", timestamp=WHEN
    )


def test_a_good_edit_has_no_warnings() -> None:
    result = check(
        reply("Hello, your order RD-10001 was delivered. Under RET-1.1 items return in 7 days.")
    )
    assert result.passed  # type: ignore[attr-defined]


def test_a_flawed_edit_is_warned_with_the_rules_that_fired() -> None:
    result = check("We will refund you today. Order RD-99999.")
    assert result.rule_ids >= {"G-3", "G-6", "G-8"}  # type: ignore[attr-defined]


def test_the_edit_is_judged_against_the_drafts_own_citations() -> None:
    uncited = EditContext(**{**edit_context().__dict__, "citations": []})
    result = validate_edit(
        reply("Your order RD-10001 is here."), uncited, sign_off=SIGN_OFF, policy_texts=POLICIES
    )
    assert result.rule_ids == {"G-1"}


def test_an_override_needs_a_reason_known_rules_and_an_actor() -> None:
    for reason in ("", "   "):
        with pytest.raises(ValidationError):
            override(["G-3"], reason)
    with pytest.raises(ValidationError):
        override([])
    with pytest.raises(ValidationError):
        override(["G-99"])
    with pytest.raises(ValidationError):
        EditOverride(reason="x", overridden_rules=["G-3"], actor="", timestamp=WHEN)


def test_an_override_is_normalised() -> None:
    made = override(["G-8", "G-3", "G-8"], "  because  ")
    assert made.overridden_rules == ["G-3", "G-8"] and made.reason == "because"


def test_an_override_must_cover_every_rule_that_fired() -> None:
    result = check("We will refund you today.")  # G-3 and G-8
    check_override(result, override(["G-3", "G-8"]))  # type: ignore[arg-type]
    with pytest.raises(OverrideError, match="G-8"):
        check_override(result, override(["G-3"]))  # type: ignore[arg-type]
    check_override(result, override(["G-3", "G-8", "G-9"]))  # type: ignore[arg-type]


def test_there_must_be_a_warning_to_override() -> None:
    result = check(reply("Hello."))
    with pytest.raises(OverrideError, match="no grounding warnings"):
        check_override(result, override(["G-3"]))  # type: ignore[arg-type]
