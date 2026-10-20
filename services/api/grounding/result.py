"""The combined grounding verdict that is stored with a draft and shown to the owner."""

from typing import Any

from pydantic import Field

from services.api.grounding.models import RuleFailure
from services.api.models.common import StrictModel
from services.api.models.judge import JudgeOutput


class GroundingResult(StrictModel):
    """Passed only when the validator found nothing and the judge ran and found nothing."""

    passed: bool
    deterministic: list[RuleFailure] = Field(default_factory=list)
    judge: JudgeOutput | None = Field(
        default=None, description="Absent when the validator failed first or the judge gave none."
    )
    judge_error: str | None = Field(
        default=None, description="Why the judge gave no verdict. The draft is blocked."
    )

    @property
    def rule_ids(self) -> set[str]:
        ids = {failure.rule_id for failure in self.deterministic}
        if self.judge is not None:
            ids.update(issue.rule_id for issue in self.judge.issues if issue.rule_id)
        return ids

    def reasons(self) -> list[str]:
        """One line per problem, for the retry prompt. Rule and reason, never customer data."""
        lines = [failure.reason() for failure in self.deterministic]
        if self.judge is not None:
            lines += [
                f"{issue.rule_id or 'judge'}: {issue.detail} ({issue.claim!r})"
                for issue in self.judge.issues
            ]
        return lines

    def audit_payload(self) -> dict[str, Any]:
        """JSON-safe form for `drafts.grounding_result` and the audit log."""
        return self.model_dump(mode="json")
