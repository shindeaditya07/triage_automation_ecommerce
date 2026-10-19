"""The classification step: ask the model what the customer wants, and fail safe.

A model that cannot give a usable answer never blocks or guesses: the first invalid answer is
tried once more with the field errors put in front of the model, and a second one (or any other
model failure) escalates the ticket to the owner with the reason recorded. Deciding whether a
*valid* classification also needs escalating (low confidence, a legal threat) is the next step,
`escalation/pre_lookup.py`; this step only produces the classification.
"""

from dataclasses import dataclass, field

from services.api.llm.client import CallRecord, ClassifyRequest, LLMClient
from services.api.llm.errors import LLMError, LLMInvalidOutputError
from services.api.models.enums import Flag
from services.api.models.ticket import Classification

INVALID_OUTPUT_REASON = "classification_invalid_output"
# `high_value` is decided after the order is read, from the order's value. A model that sets it
# from the message alone is guessing, so the flag is removed here.
SET_LATER = frozenset({Flag.HIGH_VALUE})


@dataclass(frozen=True)
class ClassificationStage:
    """Either a classification, or the reason the ticket must be escalated instead."""

    classification: Classification | None = None
    escalation_reason: str | None = None
    calls: list[CallRecord] = field(default_factory=list)
    dropped_flags: list[str] = field(default_factory=list)


def _clean(classification: Classification) -> tuple[Classification, list[str]]:
    kept: list[Flag] = []
    for flag in classification.flags:
        if flag not in SET_LATER and flag not in kept:
            kept.append(flag)
    dropped = sorted({flag.value for flag in classification.flags if flag in SET_LATER})
    return classification.model_copy(update={"flags": kept}), dropped


async def classify_ticket(
    client: LLMClient, ticket_id: str, subject: str | None, body: str
) -> ClassificationStage:
    previous_error: str | None = None
    for attempt in (1, 2):
        request = ClassifyRequest(
            ticket_id=ticket_id, subject=subject, body=body, previous_error=previous_error
        )
        try:
            result = await client.classify(request)
        except LLMInvalidOutputError as exc:
            if attempt == 2:
                return ClassificationStage(escalation_reason=INVALID_OUTPUT_REASON)
            previous_error = exc.detail
            continue
        except LLMError as exc:
            return ClassificationStage(escalation_reason=exc.escalation_reason)
        classification, dropped = _clean(result.value)
        return ClassificationStage(
            classification=classification, calls=[result.record], dropped_flags=dropped
        )
    raise AssertionError("unreachable")  # pragma: no cover
