"""Save an edit, approve or reject a ticket's draft. Owner-only; see review/service.py for the
rules. Approving records the decision and nothing more: no reply is sent."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from services.api.auth.routes import require_owner
from services.api.auth.service import Owner
from services.api.review.models import (
    ApproveRequest,
    RejectRequest,
    ReviewOutcome,
    SaveEditRequest,
)
from services.api.review.service import Conflict, Invalid, NoDraft, ReviewService

router = APIRouter(prefix="/api/v1/tickets", tags=["review"])


def get_review(request: Request) -> ReviewService:
    service: ReviewService | None = getattr(request.app.state, "review", None)
    if service is None:
        raise HTTPException(503, "review is not available")
    return service


Service = Annotated[ReviewService, Depends(get_review)]
Who = Annotated[Owner, Depends(require_owner)]


@router.patch("/{ticket_id}/draft", response_model=ReviewOutcome)
async def save_edit(
    ticket_id: UUID, body: SaveEditRequest, owner: Who, service: Service
) -> ReviewOutcome:
    try:
        return await service.save_edit(ticket_id, body, owner)
    except NoDraft:
        raise HTTPException(404, "no draft for this ticket") from None
    except Invalid as exc:
        raise HTTPException(
            422,
            {"message": str(exc), "failures": [f.model_dump(mode="json") for f in exc.failures]},
        ) from None
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/{ticket_id}/approve", response_model=ReviewOutcome)
async def approve(
    ticket_id: UUID, body: ApproveRequest, owner: Who, service: Service
) -> ReviewOutcome:
    try:
        return await service.approve(ticket_id, body, owner)
    except NoDraft:
        raise HTTPException(404, "no draft for this ticket") from None
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/{ticket_id}/reject", response_model=ReviewOutcome)
async def reject(
    ticket_id: UUID, body: RejectRequest, owner: Who, service: Service
) -> ReviewOutcome:
    try:
        return await service.reject(ticket_id, body, owner)
    except NoDraft:
        raise HTTPException(404, "no draft for this ticket") from None
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None
