"""Checking an owner's edit of a draft (spec 13.4).

The deterministic validator re-runs on the edited text. Its result is a warning: it never stops
the owner from saving. To save despite a warning the owner gives a reason, recorded in the audit
log as an `EditOverride` that names every rule being overridden. The judge is not run on edits.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from services.api.grounding.config import GroundingConfig
from services.api.grounding.models import RULES, ValidationResult
from services.api.grounding.validator import GroundingContext, validate
from services.api.models.common import StrictModel, UtcDatetime
from services.api.models.draft import Citation


class OverrideError(ValueError):
    """The override does not cover what the edit is being warned about."""


@dataclass(frozen=True)
class EditContext:
    """What an edit is checked against: the ticket, the draft being edited and its sources."""

    draft_id: UUID
    customer_email: str
    subject: str | None
    body: str
    order: dict[str, Any] | None  # the order snapshot the draft was made from
    citations: list[Citation]  # the draft's own citations, which the edit keeps


class EditOverride(StrictModel):
    """The owner saved an edit despite grounding warnings, and why."""

    reason: str
    overridden_rules: list[str] = Field(min_length=1)
    actor: str = Field(min_length=1)
    timestamp: UtcDatetime

    @field_validator("reason")
    @classmethod
    def _reason_is_given(cls, reason: str) -> str:
        if not reason.strip():
            raise ValueError("an override needs a reason")
        return reason.strip()

    @field_validator("overridden_rules")
    @classmethod
    def _known_rules(cls, rules: list[str]) -> list[str]:
        unknown = sorted(set(rules) - set(RULES))
        if unknown:
            raise ValueError(f"not grounding rules: {', '.join(unknown)}")
        return sorted(set(rules), key=RULES.index)


def validate_edit(
    reply_text: str,
    edit: EditContext,
    *,
    sign_off: str,
    policy_texts: Mapping[str, str],
    config: GroundingConfig | None = None,
) -> ValidationResult:
    context = GroundingContext(
        order=edit.order,
        customer_email=edit.customer_email,
        policy_texts=policy_texts,
        sign_off=sign_off,
        customer_message="\n".join(part for part in (edit.subject, edit.body) if part),
    )
    return validate(reply_text, edit.citations, context, config)


def check_override(result: ValidationResult, override: EditOverride) -> None:
    """Raise unless there is a warning to override and the override names every rule that fired."""
    if result.passed:
        raise OverrideError("the edit has no grounding warnings to override")
    missing = sorted(result.rule_ids - set(override.overridden_rules), key=RULES.index)
    if missing:
        raise OverrideError(f"the override does not cover: {', '.join(missing)}")
