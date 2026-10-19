"""What the LLM judge returns (spec section 13.2). It can block a draft; it cannot approve one."""

from pydantic import Field, model_validator

from services.api.models.common import StrictModel
from services.api.models.ticket import Probability


class JudgeIssue(StrictModel):
    rule_id: str | None = Field(
        default=None, description="The grounding rule this breaks, e.g. 'G-5', if one applies."
    )
    claim: str = Field(min_length=1, description="The sentence or phrase in the reply.")
    detail: str = Field(min_length=1, description="Why it is not supported or not allowed.")


class JudgeOutput(StrictModel):
    grounded: bool
    issues: list[JudgeIssue] = Field(default_factory=list)
    confidence: Probability

    @model_validator(mode="after")
    def _issues_match_the_verdict(self) -> "JudgeOutput":
        if self.grounded and self.issues:
            raise ValueError("a reply with issues is not grounded")
        if not self.grounded and not self.issues:
            raise ValueError("a reply that is not grounded must say what is wrong")
        return self
