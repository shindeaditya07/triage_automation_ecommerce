"""What the two intake endpoints accept. Limits here are the first line; settings set the rest."""

import re
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

from services.api.models.common import StrictModel, UtcDatetime
from services.api.models.ticket import Attachment, Customer

_CONTENT_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$")


class AttachmentIn(Attachment):
    @field_validator("content_type")
    @classmethod
    def _looks_like_a_media_type(cls, value: str) -> str:
        if not _CONTENT_TYPE.match(value):
            raise ValueError("must look like 'type/subtype'")
        return value


class FormPayload(StrictModel):
    """The public contact form. `website` is a honeypot: people never see it, bots fill it."""

    name: str | None = Field(default=None, max_length=100)
    email: EmailStr
    subject: str | None = Field(default=None, max_length=200)
    message: str = Field(min_length=1)
    order_id: str | None = Field(default=None, max_length=50)
    attachments: list[AttachmentIn] = Field(default_factory=list)
    # A value the page generates once per submission, so a double click or a retry is one ticket.
    submission_id: UUID | None = None
    website: str | None = Field(default=None, max_length=1000)


class EmailPayload(StrictModel):
    """One inbound email, as the forwarder describes it."""

    message_id: str = Field(min_length=1, max_length=255)
    received_at: UtcDatetime | None = None
    customer: Customer
    subject: str | None = Field(default=None, max_length=500)
    body: str = Field(min_length=1)
    in_reply_to: str | None = Field(default=None, max_length=255)
    order_id_hint: str | None = Field(default=None, max_length=50)
    attachments: list[AttachmentIn] = Field(default_factory=list)
