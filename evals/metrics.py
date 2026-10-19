"""Metric maths for the evaluation report. Every rate carries its Wilson interval."""

import math
from collections.abc import Callable, Sequence
from typing import Protocol

from pydantic import Field

from evals.schema import BadDraft, GroundingRule, LabeledTicket, Outcome
from services.api.models.common import StrictModel
from services.api.models.enums import Intent

Z_95 = 1.959963984540054


class Rate(StrictModel):
    """successes out of total, with the 95% Wilson score interval."""

    successes: int = Field(ge=0)
    total: int = Field(ge=0)
    rate: float | None
    low: float | None
    high: float | None


def rate(successes: int, total: int, z: float = Z_95) -> Rate:
    if not 0 <= successes <= total:
        raise ValueError(f"successes ({successes}) must be between 0 and total ({total})")
    if total == 0:
        return Rate(successes=0, total=0, rate=None, low=None, high=None)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return Rate(
        successes=successes,
        total=total,
        rate=p,
        low=max(0.0, centre - margin),
        high=min(1.0, centre + margin),
    )


class Prediction(StrictModel):
    """What a system under test says about one ticket."""

    intent: Intent
    urgency: str
    outcome: Outcome
    escalation_reasons: list[str] = Field(default_factory=list)


class IntentMetrics(StrictModel):
    intent: Intent
    support: int
    precision: Rate
    recall: Rate


class ClassificationMetrics(StrictModel):
    per_intent: list[IntentMetrics]
    accuracy: Rate
    urgency_accuracy: Rate


class EscalationMetrics(StrictModel):
    """Escalation means any route to the owner: no-draft or suggestion-only (spec section 14)."""

    recall: Rate
    precision: Rate
    no_draft_recall: Rate
    outcome_accuracy: Rate
    missed: list[str]
    false_escalations: list[str]


def classification_metrics(
    tickets: Sequence[LabeledTicket], predictions: dict[str, Prediction]
) -> ClassificationMetrics:
    per_intent = []
    for intent in Intent:
        tp = sum(1 for t in tickets if t.intent is intent and predictions[t.id].intent is intent)
        predicted = sum(1 for t in tickets if predictions[t.id].intent is intent)
        actual = sum(1 for t in tickets if t.intent is intent)
        per_intent.append(
            IntentMetrics(
                intent=intent,
                support=actual,
                precision=rate(tp, predicted),
                recall=rate(tp, actual),
            )
        )
    correct = sum(1 for t in tickets if predictions[t.id].intent is t.intent)
    urgency_ok = sum(1 for t in tickets if predictions[t.id].urgency == t.urgency)
    return ClassificationMetrics(
        per_intent=per_intent,
        accuracy=rate(correct, len(tickets)),
        urgency_accuracy=rate(urgency_ok, len(tickets)),
    )


def escalation_metrics(
    tickets: Sequence[LabeledTicket], predictions: dict[str, Prediction]
) -> EscalationMetrics:
    should = [t for t in tickets if t.outcome.is_escalation]
    did = [t for t in tickets if predictions[t.id].outcome.is_escalation]
    caught = [t for t in should if predictions[t.id].outcome.is_escalation]
    no_draft = [t for t in tickets if t.outcome is Outcome.NO_DRAFT_ESCALATION]
    no_draft_caught = [
        t for t in no_draft if predictions[t.id].outcome is Outcome.NO_DRAFT_ESCALATION
    ]
    exact = sum(1 for t in tickets if predictions[t.id].outcome is t.outcome)
    return EscalationMetrics(
        recall=rate(len(caught), len(should)),
        precision=rate(len(caught), len(did)),
        no_draft_recall=rate(len(no_draft_caught), len(no_draft)),
        outcome_accuracy=rate(exact, len(tickets)),
        missed=sorted(t.id for t in should if not predictions[t.id].outcome.is_escalation),
        false_escalations=sorted(t.id for t in did if not t.outcome.is_escalation),
    )


class GroundingValidator(Protocol):
    """The deterministic validator (arrives with the grounding branch).

    Given a draft and the order it was written against, return the rules it fails.
    """

    def __call__(self, draft: BadDraft, order: dict[str, object] | None) -> set[GroundingRule]: ...


class GroundingMetrics(StrictModel):
    caught: Rate = Field(description="Bad drafts failing at least one rule. Must be 100%.")
    rule_match: Rate = Field(description="Bad drafts where every expected rule was reported.")
    missed: list[str]


def grounding_metrics(
    drafts: Sequence[BadDraft],
    validator: GroundingValidator,
    order_for: Callable[[BadDraft], dict[str, object] | None],
) -> GroundingMetrics:
    results = {d.id: validator(d, order_for(d)) for d in drafts}
    caught = [d for d in drafts if results[d.id]]
    matched = [d for d in drafts if set(d.expected_rules) <= results[d.id]]
    return GroundingMetrics(
        caught=rate(len(caught), len(drafts)),
        rule_match=rate(len(matched), len(drafts)),
        missed=sorted(d.id for d in drafts if not results[d.id]),
    )
