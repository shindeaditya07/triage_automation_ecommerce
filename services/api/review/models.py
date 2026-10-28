"""What the owner sends to save an edit, approve, reject or reply, and what comes back."""

from uuid import UUID

from pydantic import Field, field_validator

from services.api.grounding.models import RULES
from services.api.models.common import StrictModel
from services.api.models.enums import ReviewStatus, TicketStatus

MAX_REPLY_CHARS = 20_000
MIN_REJECT_REASON_CHARS = 10


class OverrideInput(StrictModel):
    """Why the owner is saving an edit that still has grounding warnings."""

    reason: str = Field(min_length=1, max_length=1_000)
    overridden_rules: list[str] = Field(min_length=1)

    @field_validator("overridden_rules")
    @classmethod
    def _known_rules(cls, rules: list[str]) -> list[str]:
        unknown = sorted(set(rules) - set(RULES))
        if unknown:
            raise ValueError(f"not grounding rules: {', '.join(unknown)}")
        return rules


class SaveEditRequest(StrictModel):
    reply_text: str = Field(min_length=1, max_length=MAX_REPLY_CHARS)
    override: OverrideInput | None = None


class OwnerReplyRequest(StrictModel):
    """The owner's own reply to an escalated ticket, which has no draft."""

    reply_text: str = Field(min_length=1, max_length=MAX_REPLY_CHARS)
    original_viewed: bool = False  # the owner has opened the customer's original message
    override: OverrideInput | None = None


class ApproveRequest(StrictModel):
    original_viewed: bool = False  # the owner has opened the customer's original message
    confirm_suggestion_only: bool = False  # the owner confirmed a suggestion-only draft


class RejectRequest(StrictModel):
    reason: str = Field(max_length=1_000)

    @field_validator("reason")
    @classmethod
    def _reason_is_given(cls, reason: str) -> str:
        reason = reason.strip()
        if len(reason) < MIN_REJECT_REASON_CHARS:
            raise ValueError(f"give a reason of at least {MIN_REJECT_REASON_CHARS} characters")
        return reason


class ReviewOutcome(StrictModel):
    ticket_id: UUID
    draft_id: UUID
    ticket_status: TicketStatus
    review_status: ReviewStatus
    final_text: str | None
