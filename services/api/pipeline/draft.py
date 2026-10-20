"""The drafting step: write a reply from the order and the retrieved policy, and fail safe.

The model sees only what this step puts in front of it: the customer's text, this customer's order
(without their email address), and the retrieved policy passages. A model that cannot give a usable
answer never blocks or guesses: an invalid answer is tried once more with the reason put in front
of the model, and a second one (or any other model or retrieval failure) escalates the ticket to
the owner with the reason recorded.

This step only writes the draft. Whether the draft is grounded in its sources is checked by the
grounding validator, which is the next step; until it exists a stored draft is unchecked.
"""

from dataclasses import dataclass, field
from uuid import UUID

from services.api.context.lookup import ContextLookup
from services.api.llm.client import CallRecord, DraftRequest, LLMClient, Passage
from services.api.llm.errors import LLMError, LLMInvalidOutputError
from services.api.models.draft import DraftOutput
from services.api.pipeline.store import TicketText
from services.api.retrieval.index import PolicyIndex
from services.api.retrieval.retrieve import RetrievalError, retrieve

INVALID_OUTPUT_REASON = "draft_invalid_output"
RETRIEVAL_FAILED_REASON = "retrieval_failed"
OUTSIDE_POLICY_PASSAGE = "ESC-8.3"  # what a suggestion-only draft must be able to cite


@dataclass(frozen=True)
class DraftingConfig:
    """What the step needs besides the ticket: where the policy is, and who the reply is from."""

    index: PolicyIndex
    sign_off: str


@dataclass(frozen=True)
class DraftStage:
    """Either a draft, or the reason the ticket must be escalated instead."""

    draft: DraftOutput | None = None
    passage_ids: list[str] = field(default_factory=list)
    calls: list[CallRecord] = field(default_factory=list)
    escalation_reason: str | None = None


def order_for_model(lookup: ContextLookup) -> dict[str, object] | None:
    """The order as the model sees it. The customer's email address is the sender's own and the
    reply does not need it, so it stays out."""
    if lookup.order is None:
        return None
    return lookup.order.model_dump(mode="json", exclude={"customer_email"})


async def draft_ticket(
    client: LLMClient,
    config: DraftingConfig,
    ticket: TicketText,
    lookup: ContextLookup,
    *,
    snapshot_id: UUID | None = None,
    suggestion_only: bool = False,
) -> DraftStage:
    if ticket.classification is None:
        raise ValueError("a ticket is drafted only once it is classified")
    extra = (OUTSIDE_POLICY_PASSAGE,) if suggestion_only else ()
    try:
        retrieval = await retrieve(
            config.index,
            client,
            subject=ticket.subject,
            body=ticket.body,
            intent=ticket.classification.intent,
            extra_ids=extra,
        )
    except LLMError as exc:
        return DraftStage(escalation_reason=exc.escalation_reason)
    except RetrievalError:
        return DraftStage(escalation_reason=RETRIEVAL_FAILED_REASON)

    calls = [retrieval.call]
    passages = [Passage(policy_id=p.policy_id, text=p.text) for p in retrieval.passages]
    failures: list[str] = []
    for attempt in (1, 2):
        request = DraftRequest(
            ticket_id=str(ticket.ticket_id),
            subject=ticket.subject,
            body=ticket.body,
            order=order_for_model(lookup),
            order_snapshot_id=snapshot_id,
            passages=passages,
            policy_version=retrieval.policy_version,
            sign_off=config.sign_off,
            failures=failures,
        )
        try:
            result = await client.draft(request)
        except LLMInvalidOutputError as exc:
            if attempt == 2:
                return DraftStage(escalation_reason=INVALID_OUTPUT_REASON, calls=calls)
            failures = [exc.detail] if exc.detail else ["the previous answer was not usable"]
            continue
        except LLMError as exc:
            return DraftStage(escalation_reason=exc.escalation_reason, calls=calls)
        # What the draft was made from is recorded from this step's own inputs, not from what
        # the client reports back.
        draft = result.value.model_copy(
            update={"order_snapshot_id": snapshot_id, "policy_version": retrieval.policy_version}
        )
        return DraftStage(
            draft=draft,
            passage_ids=retrieval.policy_ids,
            calls=[*calls, result.record],
        )
    raise AssertionError("unreachable")  # pragma: no cover
