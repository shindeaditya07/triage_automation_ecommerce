"""What the metrics dashboard reads (spec section 18). Everything is computed from stored data."""

from datetime import date

from pydantic import Field

from services.api.models.common import StrictModel


class Count(StrictModel):
    key: str
    count: int


class IntentUrgency(StrictModel):
    """One bar of the intent chart, split by urgency. `none` is a ticket not yet classified."""

    intent: str
    by_urgency: dict[str, int]


class Spread(StrictModel):
    """A time distribution in seconds. Both are None when there is nothing to measure."""

    samples: int
    p50: float | None
    p95: float | None


class Rate(StrictModel):
    numerator: int
    denominator: int
    rate: float | None = Field(description="None when the denominator is zero.")


class Escalations(StrictModel):
    escalated: int
    total: int
    rate: float | None
    by_reason: list[Count]


class CostDay(StrictModel):
    day: date
    tickets: int
    cost_inr: float
    per_ticket_inr: float | None


class Cost(StrictModel):
    total_inr: float
    per_ticket_inr: float | None
    input_tokens: int
    output_tokens: int
    per_day: list[CostDay]


class MetricsReport(StrictModel):
    start: date = Field(description="First day included, in IST.")
    end: date = Field(description="Last day included, in IST.")
    tickets_total: int
    by_intent: list[Count]
    by_urgency: list[Count]
    intent_urgency: list[IntentUrgency]
    draft_acceptance: Rate = Field(
        description="Approved drafts whose final text equals the draft (whitespace ignored)."
    )
    time_to_draft_seconds: Spread
    time_to_send_business_seconds: Spread = Field(
        description="Draft in queue to mock send, counting only Monday to Saturday 10:00-18:00 IST."
    )
    grounding_failures: list[Count] = Field(description="Every rule G-1 to G-10, zeros included.")
    escalations: Escalations
    fallback: Rate = Field(description="Drafted tickets that ended on the fallback template.")
    cost: Cost
    policy_coverage: list[Count]
