"""What the owner's queue and ticket screen read. Only fields the screens show."""

from typing import Any
from uuid import UUID

from pydantic import Field

from services.api.models.common import StrictModel, UtcDatetime
from services.api.models.draft import Citation
from services.api.models.enums import (
    Channel,
    DraftKind,
    Intent,
    ProposedAction,
    ReviewStatus,
    Sentiment,
    TicketStatus,
    Urgency,
)


class TicketSummary(StrictModel):
    ticket_id: UUID
    complaint_id: int
    received_at: UtcDatetime
    channel: Channel
    customer_email: str
    customer_name: str | None
    subject: str | None
    status: TicketStatus
    intent: Intent | None
    urgency: Urgency | None
    escalation_reason: str | None
    flags: list[str]
    has_draft: bool
    suggestion_only: bool
    used_fallback_template: bool


class TicketPage(StrictModel):
    items: list[TicketSummary]
    total: int
    limit: int
    offset: int


class CitedPolicy(StrictModel):
    policy_id: str
    text: str | None  # None when the passage is not in the loaded policy set


class DraftView(StrictModel):
    draft_id: UUID
    attempt: int
    kind: DraftKind
    suggestion_only: bool
    reply_text: str
    final_text: str | None
    citations: list[Citation]
    cited_policies: list[CitedPolicy]
    proposed_action: ProposedAction
    confidence: float
    model: str
    policy_version: str
    grounding_result: dict[str, Any] | None
    review_status: ReviewStatus
    reviewed_by: str | None
    reviewed_at: UtcDatetime | None
    created_at: UtcDatetime | None


class TicketDetail(StrictModel):
    ticket_id: UUID
    complaint_id: int
    thread_id: UUID
    received_at: UtcDatetime
    channel: Channel
    customer_email: str
    customer_name: str | None
    subject: str | None
    body: str
    order_id_hint: str | None
    has_photo: bool
    status: TicketStatus
    intent: Intent | None
    urgency: Urgency | None
    confidence: float | None
    sentiment: Sentiment | None
    flags: list[str]
    escalation_reason: str | None
    failure_reason: str | None
    order: dict[str, Any] | None  # the snapshot the draft was made from
    draft: DraftView | None
    needs_original_viewed: bool = Field(
        description="Escalated or suspected-injection: the owner must view the original message "
        "before approving."
    )


class AuditEvent(StrictModel):
    event_id: int
    event_type: str
    actor: str
    occurred_at: UtcDatetime
    payload: dict[str, Any]  # what the step recorded: reasons and call records, not rejected text
