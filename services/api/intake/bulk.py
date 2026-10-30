"""`POST /api/v1/tickets/bulk`: the owner uploads many complaints at once (a spreadsheet of test
complaints, or a backlog). Each row is a complaint exactly as the contact form takes it and goes
through the same intake as a form submission, so it is cleaned, de-duplicated and queued the same
way. Owner-only, so it has no per-address rate limit; a bad row is reported and the rest go on.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import Field, ValidationError

from services.api.auth.routes import require_owner
from services.api.auth.service import Owner
from services.api.intake.cleaning import EmptyMessageError
from services.api.intake.payloads import FormPayload
from services.api.intake.routes import Intake
from services.api.models.common import StrictModel
from services.api.models.enums import Channel
from services.api.models.ticket import Customer, TicketCreateRequest

router = APIRouter(prefix="/api/v1/tickets", tags=["intake"])

MAX_COMPLAINTS = 250
FIELDS = {"name", "email", "subject", "message", "order_id"}  # what a complaint row may carry


class BulkRequest(StrictModel):
    # One id per upload: sending the same batch again makes no second ticket for any row.
    batch_id: UUID
    complaints: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_COMPLAINTS)


class RejectedRow(StrictModel):
    row: int  # 1 is the first complaint in this request
    reason: str


class BulkResult(StrictModel):
    received: int  # new tickets
    duplicates: int  # rows already received from the same batch
    rejected: list[RejectedRow]


def _reason(exc: ValidationError) -> str:
    first = exc.errors(include_input=False, include_url=False, include_context=False)[0]
    where = ".".join(str(part) for part in first["loc"]) or "row"
    return f"{where}: {first['msg']}"


@router.post("/bulk", response_model=BulkResult)
async def upload_complaints(
    batch: BulkRequest,
    owner: Annotated[Owner, Depends(require_owner)],
    runtime: Intake,
) -> BulkResult:
    received = duplicates = 0
    rejected: list[RejectedRow] = []
    for row, item in enumerate(batch.complaints, start=1):
        unknown = sorted(set(item) - FIELDS)
        if unknown:
            rejected.append(RejectedRow(row=row, reason=f"unknown field: {', '.join(unknown)}"))
            continue
        try:
            form = FormPayload.model_validate(item)
        except ValidationError as exc:
            rejected.append(RejectedRow(row=row, reason=_reason(exc)))
            continue
        if len(form.message) > runtime.settings.form_message_max_chars:
            rejected.append(RejectedRow(row=row, reason="message is too long"))
            continue
        try:
            result = await runtime.service.ingest(
                TicketCreateRequest(
                    external_message_id=f"bulk-{batch.batch_id}-{row}",
                    channel=Channel.FORM,
                    customer=Customer(email=form.email, name=form.name),
                    subject=form.subject,
                    body=form.message,
                    order_id_hint=form.order_id,
                )
            )
        except EmptyMessageError:
            rejected.append(RejectedRow(row=row, reason="message is empty"))
            continue
        if result.created:
            received += 1
        else:
            duplicates += 1
    return BulkResult(received=received, duplicates=duplicates, rejected=rejected)
