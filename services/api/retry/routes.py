"""Retry a failed ticket. Owner-only; see retry/service.py."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from services.api.auth.routes import require_owner
from services.api.auth.service import Owner
from services.api.retry.service import RetryResult, RetryService
from services.api.retry.store import NoSuchTicket, NotRetryable

router = APIRouter(prefix="/api/v1/tickets", tags=["retry"])


def get_retry(request: Request) -> RetryService:
    service: RetryService | None = getattr(request.app.state, "retry", None)
    if service is None:
        raise HTTPException(503, "retry is not available")
    return service


@router.post("/{ticket_id}/retry", response_model=RetryResult)
async def retry_ticket(
    ticket_id: UUID,
    owner: Annotated[Owner, Depends(require_owner)],
    service: Annotated[RetryService, Depends(get_retry)],
) -> RetryResult:
    """Run a failed or model-escalated ticket again from where it stopped. 409 otherwise."""
    try:
        return await service.retry(ticket_id, owner)
    except NoSuchTicket:
        raise HTTPException(404, "no such ticket") from None
    except NotRetryable:
        raise HTTPException(
            409, "only a failed ticket, or an escalated one with no draft, can be retried"
        ) from None
