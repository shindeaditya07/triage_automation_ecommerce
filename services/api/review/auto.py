"""When a drafted reply is approved without the owner (spec: human review is the default).

A reply is auto-approved only when nothing about it asks for a person: the model wrote it (not the
fallback template), it is not suggestion-only, the ticket carries no escalation reason or injection
flag, and both the classifier and the drafter are at least `threshold` sure. `auto_approve_block`
returns why a draft must wait for the owner, or None when it may go.
"""

from services.api.models.enums import DraftKind, Flag
from services.api.review.store import ReviewState

AUTO_APPROVER = "system:auto-approve"  # who the audit trail and the draft say approved it


def confidence_of(state: ReviewState) -> float | None:
    """The lower of the classifier's and the drafter's confidence; None when either is unknown."""
    if state.classifier_confidence is None:
        return None
    return min(state.classifier_confidence, state.draft_confidence)


def auto_approve_block(state: ReviewState, threshold: float) -> str | None:
    if not state.open:
        return "the draft is not waiting for review"
    if state.draft_kind is not DraftKind.LLM:
        return "the reply is not one the model wrote"
    if state.suggestion_only:
        return "the draft is suggestion-only"
    if state.escalation_reason is not None:
        return "the ticket was escalated"
    if Flag.PROMPT_INJECTION_SUSPECTED.value in state.flags:
        return "the message looks like an injection attempt"
    if state.final_text is not None:
        return "the owner has already edited the draft"
    confidence = confidence_of(state)
    if confidence is None:
        return "the ticket has no classifier confidence"
    if confidence < threshold:
        return f"confidence {confidence:.2f} is below {threshold:.2f}"
    return None
