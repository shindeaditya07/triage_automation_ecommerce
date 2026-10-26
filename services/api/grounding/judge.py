"""The LLM judge: a second reader of a draft that the deterministic validator has already passed.

It looks for what the word and number rules cannot see, such as a promise worded in a way no list
anticipates, or a claim the sources only seem to support (spec section 13.2). It can add a block.
It never removes one: `check` (services/api/grounding/check.py) does not call it when the
validator has failed, and a judge that cannot answer blocks the draft rather than waving it
through.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from services.api.llm.client import CallRecord, JudgeRequest, LLMClient, Passage
from services.api.llm.errors import LLMError
from services.api.models.draft import Citation
from services.api.models.judge import JudgeOutput


@dataclass(frozen=True)
class JudgeStage:
    """The judge's verdict, or the reason it gave none."""

    output: JudgeOutput | None = None
    call: CallRecord | None = None
    error: str | None = None


async def judge_reply(
    client: LLMClient,
    reply_text: str,
    citations: Sequence[Citation],
    order: dict[str, Any] | None,
    passages: Sequence[Passage],
) -> JudgeStage:
    """Ask the judge model about one reply. Any model failure becomes `error`, never a pass."""
    request = JudgeRequest(
        reply_text=reply_text,
        citations=list(citations),
        order=order,
        passages=list(passages),
    )
    try:
        result = await client.judge(request)
    except LLMError as exc:
        return JudgeStage(error=exc.escalation_reason)
    return JudgeStage(output=result.value, call=result.record)
