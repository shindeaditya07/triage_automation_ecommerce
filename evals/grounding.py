"""The deterministic grounding validator, run over the seeded bad drafts and the good ones."""

from dataclasses import replace
from functools import lru_cache

from evals import corpus
from evals.schema import BadDraft, GroundingRule
from services.api.grounding.validator import GroundingContext, validate
from services.api.models.draft import Citation
from services.api.policy_source import parse_passages, policies_path
from services.api.settings import Settings

STRANGER = "someone@example.com"  # the sender when a draft is not tied to a mock-store case


@lru_cache(maxsize=1)
def _policy_texts() -> dict[str, str]:
    return dict(parse_passages(policies_path().read_text(encoding="utf-8")))


def sign_off() -> str:
    return str(Settings.model_fields["store_sign_off"].default)


def context_for(case: int | None, order: dict[str, object] | None) -> GroundingContext:
    sender = corpus.scenario(case).customer_email if case is not None else STRANGER
    return GroundingContext(
        order=order,
        customer_email=sender,
        policy_texts=_policy_texts(),
        sign_off=sign_off(),
    )


def failing_rules(draft: BadDraft, order: dict[str, object] | None) -> set[GroundingRule]:
    """The rules a seeded draft fails. The placeholders in its text are filled from its case."""
    reply = corpus.resolve(draft.reply_text, draft.order_case)
    citations = [Citation.model_validate(c.model_dump()) for c in draft.citations]
    result = validate(reply, citations, context_for(draft.order_case, order))
    return result.rule_ids  # type: ignore[return-value]


def blocked_good_drafts() -> dict[str, list[str]]:
    """Good drafts the validator blocks, with the reasons. Anything here is a false positive."""
    blocked: dict[str, list[str]] = {}
    for draft in corpus.load_good_drafts():
        reply = corpus.resolve(draft.reply_text, draft.order_case)
        order = corpus.order_json_for_case(draft.order_case)
        message = (
            corpus.resolve(draft.customer_message, draft.order_case)
            if draft.customer_message
            else None
        )
        context = replace(context_for(draft.order_case, order), customer_message=message)
        citations = [Citation.model_validate(c.model_dump()) for c in draft.citations]
        result = validate(reply, citations, context)
        if not result.passed:
            blocked[draft.id] = result.reasons()
    return blocked
