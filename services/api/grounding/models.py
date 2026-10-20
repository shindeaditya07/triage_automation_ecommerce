"""What the validator returns: named rule failures, never a bare pass or fail."""

from pydantic import Field

from services.api.models.common import StrictModel

RULES = ("G-1", "G-2", "G-3", "G-4", "G-5", "G-6", "G-7", "G-8", "G-9", "G-10")


class RuleFailure(StrictModel):
    rule_id: str = Field(pattern=r"^G-(?:[1-9]|10)$")
    span: str = Field(description="The part of the reply (or the citation) that failed.")
    detail: str
    citation_missing: bool = Field(
        default=False, description="True when the value is right but no citation backs it."
    )

    def reason(self) -> str:
        """One line for the retry prompt. It names the rule and the span, never customer data."""
        return f"{self.rule_id}: {self.detail} ({self.span!r})"


class ValidationResult(StrictModel):
    failures: list[RuleFailure] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def rule_ids(self) -> set[str]:
        return {failure.rule_id for failure in self.failures}

    def reasons(self) -> list[str]:
        return [failure.reason() for failure in self.failures]
