"""Everything done to a message before it is stored: sanitise, mask, validate."""

import posixpath
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from services.api.intake.pii import mask_pii
from services.api.intake.sanitize import clean_text
from services.api.models.ticket import Attachment, Customer, TicketCreateRequest

MAX_NAME_LENGTH = 100
MAX_SUBJECT_LENGTH = 200
_ORDER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_/#-]{0,49}$")


class EmptyMessageError(ValueError):
    """Nothing was left of the message once markup, links and hidden text were removed."""


@dataclass
class CleaningReport:
    """What cleaning did, as counts and flags only. Never the text itself."""

    pii_masked: dict[str, int] = field(default_factory=dict)
    links_removed: int = 0
    html_found: bool = False
    hidden_text_removed: bool = False
    attachments: int = 0

    def as_payload(self, channel: str) -> dict[str, Any]:
        return {
            "channel": channel,
            "pii_masked": self.pii_masked,
            "links_removed": self.links_removed,
            "html_found": self.html_found,
            "hidden_text_removed": self.hidden_text_removed,
            "attachments": self.attachments,
        }


def _text(raw: str, report: CleaningReport, limit: int | None = None) -> str:
    cleaned = clean_text(raw)
    masked = mask_pii(cleaned.text)
    report.links_removed += cleaned.links_removed
    report.html_found |= cleaned.html_found
    report.hidden_text_removed |= cleaned.hidden_text_removed
    for kind, count in masked.counts.items():
        report.pii_masked[kind] = report.pii_masked.get(kind, 0) + count
    return masked.text[:limit] if limit else masked.text


def _filename(raw: str) -> str:
    """The file's own name: no folders, no markup, no control characters."""
    name = posixpath.basename(clean_text(raw.replace("\\", "/")).text)
    return name[:255] or "attachment"


def clean_request(
    request: TicketCreateRequest, now: datetime
) -> tuple[TicketCreateRequest, CleaningReport]:
    """The same request with its text made safe, and a report of what was changed.

    Raises `EmptyMessageError` if no body is left. The sender's address is not altered: replies go
    to it, and it is the key for matching a customer's order.
    """
    report = CleaningReport()
    body = _text(request.body, report)
    if not body:
        raise EmptyMessageError("the message is empty once markup and links are removed")
    subject = _text(request.subject, report, MAX_SUBJECT_LENGTH) if request.subject else None
    name = _text(request.customer.name, report, MAX_NAME_LENGTH) if request.customer.name else None
    hint = request.order_id_hint.strip() if request.order_id_hint else None
    attachments = [
        Attachment(
            filename=_filename(item.filename),
            content_type=item.content_type.lower(),
            size_bytes=item.size_bytes,
        )
        for item in request.attachments
    ]
    report.attachments = len(attachments)
    received = request.received_at or now
    cleaned = TicketCreateRequest(
        external_message_id=request.external_message_id.strip(),
        channel=request.channel,
        received_at=min(received, now),  # a timestamp from the future is not believed
        customer=Customer(email=request.customer.email, name=name or None),
        subject=subject or None,
        body=body,
        in_reply_to=request.in_reply_to.strip() if request.in_reply_to else None,
        order_id_hint=hint if hint and _ORDER_ID.match(hint) else None,
        attachments=attachments,
    )
    return cleaned, report
