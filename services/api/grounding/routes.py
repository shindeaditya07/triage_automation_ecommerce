"""`POST /api/v1/tickets/{ticket_id}/draft/validate`: the grounding warning for an owner's edit.

Owner-only. It always answers 200 with the failures (possibly none): a warning never blocks
saving. A ticket with no draft is a 404.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from services.api.auth.routes import require_owner
from services.api.auth.service import Owner
from services.api.grounding.config import GroundingConfig
from services.api.grounding.edit import EditContext, validate_edit
from services.api.grounding.models import RuleFailure
from services.api.models.common import StrictModel

router = APIRouter(prefix="/api/v1/tickets", tags=["drafts"])

MAX_REPLY_CHARS = 20_000  # far above the grounding limit, so an overlong edit is warned (G-10)


class EditContextSource(Protocol):
    async def edit_context(self, ticket_id: UUID) -> EditContext | None: ...


@dataclass
class EditValidationRuntime:
    source: EditContextSource
    sign_off: str
    policy_texts: Mapping[str, str]
    config: GroundingConfig | None = None


class ValidateRequest(StrictModel):
    reply_text: str = Field(min_length=1, max_length=MAX_REPLY_CHARS)


class ValidateResponse(StrictModel):
    passed: bool
    failures: list[RuleFailure]


def get_edit_validation(request: Request) -> EditValidationRuntime:
    runtime: EditValidationRuntime | None = getattr(request.app.state, "edit_validation", None)
    if runtime is None:
        raise HTTPException(503, "draft validation is not available")
    return runtime


@router.post("/{ticket_id}/draft/validate", response_model=ValidateResponse)
async def validate_draft(
    ticket_id: UUID,
    body: ValidateRequest,
    owner: Annotated[Owner, Depends(require_owner)],
    runtime: Annotated[EditValidationRuntime, Depends(get_edit_validation)],
) -> ValidateResponse:
    edit = await runtime.source.edit_context(ticket_id)
    if edit is None:
        raise HTTPException(404, "no draft for this ticket")
    result = validate_edit(
        body.reply_text,
        edit,
        sign_off=runtime.sign_off,
        policy_texts=runtime.policy_texts,
        config=runtime.config,
    )
    return ValidateResponse(passed=result.passed, failures=result.failures)
