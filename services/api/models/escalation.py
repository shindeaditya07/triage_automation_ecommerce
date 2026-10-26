"""Escalation outcome (spec section 14)."""

from pydantic import Field, model_validator

from services.api.models.common import StrictModel
from services.api.models.enums import EscalationStage, Flag


class EscalationResult(StrictModel):
    escalate: bool
    stage: EscalationStage
    reason: str | None = None
    suggestion_only: bool = Field(
        default=False,
        description="True when the owner gets a draft that cannot be one-click approved.",
    )
    flags: list[Flag] = Field(default_factory=list)

    @model_validator(mode="after")
    def _escalation_needs_a_reason(self) -> "EscalationResult":
        if self.escalate and not self.reason:
            raise ValueError("an escalation needs a reason")
        if self.suggestion_only and not self.escalate:
            raise ValueError("suggestion_only only applies to an escalation")
        return self
