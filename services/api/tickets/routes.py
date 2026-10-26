"""`GET /api/v1/tickets` (the queue), `GET /api/v1/tickets/{ticket_id}` (one ticket) and
`GET /api/v1/tickets/{ticket_id}/audit` (what happened to it, oldest first).

Owner-only. The queue is sorted most urgent first, oldest first within an urgency. Reads only:
approving, editing and rejecting are separate routes.
"""

from typing import Annotated, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from services.api.auth.routes import require_owner
from services.api.auth.service import Owner
from services.api.models.enums import Intent, TicketStatus
from services.api.tickets.models import AuditEvent, TicketDetail, TicketPage
from services.api.tickets.store import TicketFilter

router = APIRouter(prefix="/api/v1/tickets", tags=["tickets"])

DEFAULT_LIMIT = 50
MAX_LIMIT = 100


class TicketReader(Protocol):
    async def list_tickets(self, flt: TicketFilter, limit: int, offset: int) -> TicketPage: ...

    async def get_ticket(self, ticket_id: UUID) -> TicketDetail | None: ...

    async def get_audit(self, ticket_id: UUID) -> list[AuditEvent] | None: ...


def get_reader(request: Request) -> TicketReader:
    reader: TicketReader | None = getattr(request.app.state, "ticket_reader", None)
    if reader is None:
        raise HTTPException(503, "tickets are not available")
    return reader


@router.get("", response_model=TicketPage)
async def list_tickets(
    owner: Annotated[Owner, Depends(require_owner)],
    reader: Annotated[TicketReader, Depends(get_reader)],
    status: Annotated[list[TicketStatus] | None, Query()] = None,
    intent: Annotated[Intent | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TicketPage:
    return await reader.list_tickets(TicketFilter(tuple(status or ()), intent), limit, offset)


@router.get("/{ticket_id}", response_model=TicketDetail)
async def get_ticket(
    ticket_id: UUID,
    owner: Annotated[Owner, Depends(require_owner)],
    reader: Annotated[TicketReader, Depends(get_reader)],
) -> TicketDetail:
    detail = await reader.get_ticket(ticket_id)
    if detail is None:
        raise HTTPException(404, "no such ticket")
    return detail


@router.get("/{ticket_id}/audit", response_model=list[AuditEvent])
async def get_ticket_audit(
    ticket_id: UUID,
    owner: Annotated[Owner, Depends(require_owner)],
    reader: Annotated[TicketReader, Depends(get_reader)],
) -> list[AuditEvent]:
    events = await reader.get_audit(ticket_id)
    if events is None:
        raise HTTPException(404, "no such ticket")
    return events
