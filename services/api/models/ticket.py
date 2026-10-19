"""Ticket schema (spec section 11)."""

from typing import Annotated
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

from services.api.models.common import StrictModel, UtcDatetime
from services.api.models.enums import Channel, Flag, Intent, Sentiment, TicketStatus, Urgency

Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class Attachment(StrictModel):
    """Metadata only; image content is not analysed in v1."""

    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)

    @property
    def is_image(self) -> bool:
        return self.content_type.lower().startswith("image/")


class Customer(StrictModel):
    email: EmailStr
    name: str | None = None

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        return value.strip().lower()


class Classification(StrictModel):
    intent: Intent
    urgency: Urgency
    confidence: Probability
    sentiment: Sentiment
    flags: list[Flag] = Field(default_factory=list)


class TicketCreateRequest(StrictModel):
    """What intake normalises both channels into before a ticket exists."""

    external_message_id: str = Field(min_length=1, max_length=255)
    channel: Channel
    received_at: UtcDatetime | None = None
    customer: Customer
    subject: str | None = None
    body: str = Field(min_length=1)
    in_reply_to: str | None = None
    order_id_hint: str | None = None
    attachments: list[Attachment] = Field(default_factory=list)


class Ticket(StrictModel):
    ticket_id: UUID
    external_message_id: str = Field(min_length=1, max_length=255)
    thread_id: UUID
    in_reply_to: str | None = None
    channel: Channel
    received_at: UtcDatetime
    customer: Customer
    subject: str | None = None
    body: str = Field(min_length=1)
    order_id_hint: str | None = None
    attachments: list[Attachment] = Field(default_factory=list)
    has_photo: bool = False
    status: TicketStatus = TicketStatus.NEW
    classification: Classification | None = None
    escalation_reason: str | None = None
    failure_reason: str | None = None
    created_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None
    deleted_at: UtcDatetime | None = None
