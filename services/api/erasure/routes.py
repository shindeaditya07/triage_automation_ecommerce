"""Erase everything one customer sent, on their request. Owner-only; see erasure/service.py."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from services.api.auth.routes import require_owner
from services.api.auth.service import Owner
from services.api.erasure.service import ErasureResult, ErasureService, InvalidEmail

router = APIRouter(prefix="/api/v1/customers", tags=["erasure"])


def get_erasure(request: Request) -> ErasureService:
    service: ErasureService | None = getattr(request.app.state, "erasure", None)
    if service is None:
        raise HTTPException(503, "erasure is not available")
    return service


@router.delete("/{email}", response_model=ErasureResult)
async def erase_customer(
    email: str,
    owner: Annotated[Owner, Depends(require_owner)],
    service: Annotated[ErasureService, Depends(get_erasure)],
) -> ErasureResult:
    """Erase every live ticket from this address. 404 when there is none, so the owner learns
    the address was mistyped or already erased."""
    try:
        result = await service.erase(email, owner)
    except InvalidEmail as exc:
        raise HTTPException(422, str(exc)) from None
    if result.erased == 0:
        raise HTTPException(404, "no tickets from this address")
    return result
