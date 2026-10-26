"""Draft output schema (spec section 12). The same models produce the LLM JSON Schema."""

from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from services.api.models.common import StrictModel, UtcDatetime
from services.api.models.enums import DraftKind, ProposedAction, ReviewStatus, SourceType
from services.api.models.ticket import Probability


class Citation(StrictModel):
    claim: str = Field(min_length=1)
    source_type: SourceType
    source_ref: str = Field(min_length=1, description="e.g. 'orders.delivered_at' or 'RET-1.1'")


class DraftOutput(StrictModel):
    """What the drafting step returns and what is stored with it for reproducibility."""

    reply_text: str = Field(min_length=1)
    citations: list[Citation] = Field(default_factory=list)
    proposed_action: ProposedAction = ProposedAction.NONE
    confidence: Probability
    prompt_version: str = Field(min_length=1)
    prompt_hash: str = Field(min_length=1)
    model: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    order_snapshot_id: UUID | None = Field(
        default=None, description="Null when no order was found for the sender."
    )


class Draft(DraftOutput):
    """A stored draft: the model output plus its place in the review workflow."""

    draft_id: UUID
    ticket_id: UUID
    attempt: int = Field(ge=1)
    kind: DraftKind = DraftKind.LLM
    suggestion_only: bool = False
    retrieved_passage_ids: list[str] = Field(default_factory=list)
    grounding_result: dict[str, Any] | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    review_status: ReviewStatus = ReviewStatus.PENDING
    final_text: str | None = None
    reviewed_by: str | None = None
    reviewed_at: UtcDatetime | None = None
    sent_at: UtcDatetime | None = None
    created_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def _review_fields_are_consistent(self) -> "Draft":
        reviewed = self.review_status != ReviewStatus.PENDING
        if reviewed != (self.reviewed_at is not None and self.reviewed_by is not None):
            raise ValueError(
                "reviewed_by and reviewed_at are set exactly when the draft is reviewed"
            )
        if self.review_status == ReviewStatus.APPROVED and not self.final_text:
            raise ValueError("an approved draft needs final_text")
        if self.sent_at is not None and self.review_status != ReviewStatus.APPROVED:
            raise ValueError("only an approved draft can be sent")
        return self
