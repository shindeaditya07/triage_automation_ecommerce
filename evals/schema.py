"""Row types for the evaluation datasets and the recorded predictions."""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator

from services.api.models.common import StrictModel
from services.api.models.draft import Citation
from services.api.models.enums import Channel, Flag, Intent, ProposedAction, Sentiment, Urgency

POLICY_ID_PATTERN = r"^[A-Z]{3}-\d+\.\d+$"
PolicyId = Annotated[str, Field(pattern=POLICY_ID_PATTERN)]
EscalationReason = Literal[
    "abusive",
    "legal_threat",
    "suspected_fraud",
    "prompt_injection",
    "non_english",
    "low_confidence",
    "intent_other",
    "order_not_found",
    "order_mismatch",
    "repeat_complaint",
    "high_value",
    "outside_policy",
]
GroundingRule = Literal["G-1", "G-2", "G-3", "G-4", "G-5", "G-6", "G-7", "G-8", "G-9", "G-10"]


class Outcome(StrEnum):
    """What the pipeline should do with a ticket (spec section 14)."""

    DRAFT = "draft"
    NO_DRAFT_ESCALATION = "no_draft_escalation"
    SUGGESTION_ONLY = "suggestion_only"

    @property
    def is_escalation(self) -> bool:
        return self is not Outcome.DRAFT


class LabeledTicket(StrictModel):
    """One synthetic ticket with its ground truth. Labels are written or reviewed by a human."""

    id: str = Field(pattern=r"^T\d{3}$")
    scenario_case: int | None = Field(
        default=None, ge=1, le=31, description="Mock-store edge case this ticket is anchored to."
    )
    channel: Channel = Channel.FORM
    customer_email: str
    customer_name: str | None = None
    subject: str | None = None
    body: str = Field(min_length=1, description="May hold {order_id}-style placeholders.")
    thread_message_count: int = Field(default=1, ge=1)
    has_photo: bool = False
    language: str = "en"
    tags: list[str] = Field(default_factory=list)

    intent: Intent
    urgency: Urgency
    sentiment: Sentiment
    flags: list[Flag] = Field(
        default_factory=list, description="Flags the classifier should raise (never high_value)."
    )
    outcome: Outcome
    escalation_reasons: list[EscalationReason] = Field(default_factory=list)
    policy_ids: list[PolicyId] = Field(default_factory=list)
    needs_review: str | None = Field(
        default=None, description="A label the author was unsure about, for the human reviewer."
    )

    @field_validator("customer_email")
    @classmethod
    def _normalised(cls, value: str) -> str:
        if value != value.strip().lower():
            raise ValueError("customer_email must be trimmed and lower-cased")
        return value

    @model_validator(mode="after")
    def _labels_agree(self) -> "LabeledTicket":
        if Flag.HIGH_VALUE in self.flags:
            raise ValueError("high_value is set after the order lookup, never by the classifier")
        if self.outcome.is_escalation != bool(self.escalation_reasons):
            raise ValueError("escalation_reasons are set exactly when the outcome is an escalation")
        if self.outcome is Outcome.SUGGESTION_ONLY and self.escalation_reasons != [
            "outside_policy"
        ]:
            raise ValueError("a suggestion-only escalation has the single reason outside_policy")
        return self


class BadDraft(StrictModel):
    """A draft with a known grounding violation. The validator must catch every one."""

    id: str = Field(pattern=r"^B\d{3}$")
    order_case: int | None = Field(ge=1, le=31, description="Mock-store case supplying the order.")
    expected_rules: list[GroundingRule] = Field(min_length=1)
    description: str
    reply_text: str = Field(min_length=1)
    citations: list[Citation] = Field(default_factory=list)
    proposed_action: ProposedAction = ProposedAction.NONE


class GoodDraft(StrictModel):
    """A correct draft. The validator must pass every one; a block here is a false positive."""

    id: str = Field(pattern=r"^V\d{3}$")
    order_case: int | None = Field(ge=1, le=31, description="Mock-store case supplying the order.")
    description: str
    customer_message: str | None = None
    reply_text: str = Field(min_length=1)
    citations: list[Citation] = Field(default_factory=list)
    proposed_action: ProposedAction = ProposedAction.NONE


class RecordedClassification(StrictModel):
    intent: Intent
    urgency: Urgency
    confidence: float = Field(ge=0, le=1)
    sentiment: Sentiment
    flags: list[Flag] = Field(default_factory=list)


class RecordedPrediction(StrictModel):
    """What the recorded run produced for one ticket (synthetic until a live model exists)."""

    ticket_id: str = Field(pattern=r"^T\d{3}$")
    classification: RecordedClassification
    outcome: Outcome
    escalation_reasons: list[EscalationReason] = Field(default_factory=list)


JsonObject = dict[str, Any]
