"""The whole grounding check for one draft: the validator first, then the judge.

The validator is the authority. If it fails, the judge is not asked, and nothing the judge could
say would change the outcome. If it passes, the judge gets a say and can only block.
"""

from collections.abc import Sequence
from typing import Any

from services.api.grounding.config import GroundingConfig
from services.api.grounding.judge import judge_reply
from services.api.grounding.result import GroundingResult
from services.api.grounding.validator import GroundingContext, validate
from services.api.llm.client import CallRecord, LLMClient, Passage
from services.api.models.draft import Citation


async def check_grounding(
    client: LLMClient,
    reply_text: str,
    citations: Sequence[Citation],
    context: GroundingContext,
    passages: Sequence[Passage],
    *,
    config: GroundingConfig | None = None,
) -> tuple[GroundingResult, list[CallRecord]]:
    """The verdict, and the judge call (if one was made) for the cost record."""
    validation = validate(reply_text, citations, context, config)
    if not validation.passed:
        return GroundingResult(passed=False, deterministic=validation.failures), []

    order: dict[str, Any] | None = None
    if context.order is not None:
        order = {key: value for key, value in context.order.items() if key != "customer_email"}
    stage = await judge_reply(client, reply_text, citations, order, passages)
    if stage.output is None:
        return GroundingResult(passed=False, judge_error=stage.error), []
    calls = [stage.call] if stage.call else []
    return GroundingResult(passed=stage.output.grounded, judge=stage.output), calls
