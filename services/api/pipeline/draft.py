"""The drafting step: write a reply from the order and the retrieved policy, check it, and fail
safe.

The model sees only what this step puts in front of it: the customer's text, this customer's order
(without their email address), and the retrieved policy passages. Every draft then goes through
the grounding check (validator, then judge). A draft that fails it is tried once more with the
reasons put in front of the model. A second failure is replaced by the fixed fallback template,
which the owner sees flagged as such. A model that cannot give a usable answer never blocks or
guesses: an invalid answer is retried once, and two of them (or any other model or retrieval
failure, or a judge that cannot answer) escalate the ticket to the owner with the reason recorded.

At most two drafts are ever asked for, so the step cannot loop.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from services.api.context.lookup import ContextLookup
from services.api.grounding.check import check_grounding
from services.api.grounding.config import GroundingConfig, default_config
from services.api.grounding.result import GroundingResult
from services.api.grounding.sign_off import with_sign_off
from services.api.grounding.validator import GroundingContext, validate
from services.api.llm.client import CallRecord, DraftRequest, LLMClient, Passage
from services.api.llm.errors import LLMError, LLMInvalidOutputError
from services.api.models.draft import DraftOutput
from services.api.models.enums import DraftKind, ProposedAction
from services.api.pipeline.store import TicketText
from services.api.policy_source import loaded_passages
from services.api.retrieval.index import PolicyIndex
from services.api.retrieval.retrieve import RetrievalError, retrieve

INVALID_OUTPUT_REASON = "draft_invalid_output"
RETRIEVAL_FAILED_REASON = "retrieval_failed"
GROUNDING_UNAVAILABLE_REASON = "grounding_unavailable"  # the judge gave no verdict
FALLBACK_INVALID_REASON = "fallback_template_invalid"  # the configured template fails the rules
OUTSIDE_POLICY_PASSAGE = "ESC-8.3"  # what a suggestion-only draft must be able to cite
MAX_ATTEMPTS = 2
FALLBACK_MODEL = "fallback-template"
FALLBACK_PROMPT_VERSION = "fallback"


@dataclass(frozen=True)
class DraftingConfig:
    """What the step needs besides the ticket: where the policy is, and who the reply is from."""

    index: PolicyIndex
    sign_off: str
    policy_texts: Mapping[str, str] = field(default_factory=loaded_passages)  # the loaded set
    grounding: GroundingConfig = field(default_factory=default_config)


@dataclass(frozen=True)
class FailedAttempt:
    """A draft attempt that did not pass. Its text is not kept, only why it failed."""

    attempt: int
    outcome: str  # "grounding_failed" or "invalid_output"
    rule_ids: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    judge_confidence: float | None = None


@dataclass(frozen=True)
class DraftStage:
    """Either a draft, or the reason the ticket must be escalated instead."""

    draft: DraftOutput | None = None
    passage_ids: list[str] = field(default_factory=list)
    calls: list[CallRecord] = field(default_factory=list)
    escalation_reason: str | None = None
    error: str | None = None  # the provider failure's own message (status, never request text)
    grounding: GroundingResult | None = None
    kind: DraftKind = DraftKind.LLM
    attempt: int = 1  # 1 or 2 for a model draft; 3 for the fallback after two failures
    failed_attempts: list[FailedAttempt] = field(default_factory=list)


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
        return DraftStage(escalation_reason=exc.escalation_reason, error=str(exc))
    except RetrievalError:
        return DraftStage(escalation_reason=RETRIEVAL_FAILED_REASON)

    calls = [retrieval.call]
    passages = [Passage(policy_id=p.policy_id, text=p.text) for p in retrieval.passages]
    context = GroundingContext(
        order=lookup.order.model_dump(mode="json") if lookup.order else None,
        customer_email=ticket.customer_email,
        policy_texts=config.policy_texts,
        sign_off=config.sign_off,
        customer_message="\n".join(part for part in (ticket.subject, ticket.body) if part),
    )
    failures: list[str] = []
    failed: list[FailedAttempt] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
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
            reason = exc.detail or "the previous answer was not usable"
            failed.append(FailedAttempt(attempt, "invalid_output", reasons=[reason]))
            if attempt < MAX_ATTEMPTS:
                failures = [reason]
                continue
            if all(f.outcome == "invalid_output" for f in failed):
                return DraftStage(escalation_reason=INVALID_OUTPUT_REASON, calls=calls)
            break  # an earlier draft failed grounding: the fallback is safer than nothing
        except LLMError as exc:
            return DraftStage(escalation_reason=exc.escalation_reason, error=str(exc), calls=calls)
        calls.append(result.record)
        # What the draft was made from is recorded from this step's own inputs, not from what
        # the client reports back.
        draft = result.value.model_copy(
            update={
                "reply_text": with_sign_off(result.value.reply_text, config.sign_off),
                "order_snapshot_id": snapshot_id,
                "policy_version": retrieval.policy_version,
            }
        )
        verdict, judge_calls = await check_grounding(
            client, draft.reply_text, draft.citations, context, passages, config=config.grounding
        )
        calls += judge_calls
        if verdict.passed:
            return DraftStage(
                draft=draft,
                passage_ids=retrieval.policy_ids,
                calls=calls,
                grounding=verdict,
                attempt=attempt,
                failed_attempts=failed,
            )
        if verdict.judge_error is not None:
            # The draft passed the rules but could not be judged. Another draft would not help.
            return DraftStage(escalation_reason=GROUNDING_UNAVAILABLE_REASON, calls=calls)
        failed.append(
            FailedAttempt(
                attempt,
                "grounding_failed",
                sorted(verdict.rule_ids),
                verdict.reasons(),
                verdict.judge.confidence if verdict.judge else None,
            )
        )
        failures = verdict.reasons()

    return _fallback(
        config, context, retrieval.policy_version, snapshot_id, retrieval.policy_ids, calls, failed
    )


def _fallback(
    config: DraftingConfig,
    context: GroundingContext,
    policy_version: str,
    snapshot_id: UUID | None,
    passage_ids: list[str],
    calls: list[CallRecord],
    failed: list[FailedAttempt],
) -> DraftStage:
    """The fixed template, after the rules have confirmed it is safe for this ticket."""
    text = config.grounding.fallback_text(config.sign_off)
    verdict = validate(text, [], context, config.grounding)
    if not verdict.passed:
        return DraftStage(escalation_reason=FALLBACK_INVALID_REASON, calls=calls)
    draft = DraftOutput(
        reply_text=text,
        proposed_action=ProposedAction.ESCALATE,
        confidence=0.0,
        prompt_version=FALLBACK_PROMPT_VERSION,
        prompt_hash=hashlib.sha256(config.grounding.fallback_template.encode()).hexdigest(),
        model=FALLBACK_MODEL,
        policy_version=policy_version,
        order_snapshot_id=snapshot_id,
    )
    return DraftStage(
        draft=draft,
        passage_ids=passage_ids,
        calls=calls,
        grounding=GroundingResult(passed=True),
        kind=DraftKind.FALLBACK_TEMPLATE,
        attempt=MAX_ATTEMPTS + 1,
        failed_attempts=failed,
    )
