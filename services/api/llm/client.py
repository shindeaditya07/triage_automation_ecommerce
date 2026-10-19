"""The one interface the rest of the application uses to talk to a model.

Application code imports this module and never a provider's SDK or HTTP details
(docs/AGENTS.md section 5). Every call returns its value together with a `CallRecord`: which
model, which prompt (name, version, hash), temperature, tokens, latency and cost in INR, so a
result can be reproduced and its cost reported.

Anything that goes wrong is an `LLMError` (services/api/llm/errors.py). The caller turns it into an
escalation with `error.escalation_reason`; a model failure never yields a draft.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from services.api.models.common import StrictModel
from services.api.models.draft import Citation, DraftOutput
from services.api.models.enums import ProposedAction
from services.api.models.judge import JudgeOutput
from services.api.models.ticket import Classification, Probability

Purpose = Literal["classify", "draft", "judge", "embed"]


class CallRecord(StrictModel):
    """What one model call cost and exactly what ran. Stored with the ticket's audit trail."""

    purpose: Purpose
    model: str
    prompt_name: str | None = None
    prompt_version: str | None = None
    prompt_hash: str | None = None
    temperature: float | None = None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    cost_inr: Decimal = Field(ge=0)
    attempts: int = Field(default=1, ge=1)

    def audit_payload(self) -> dict[str, Any]:
        """JSON-safe form for `audit_log.payload` (the cost as a string, never a float)."""
        return self.model_dump(mode="json")


@dataclass(frozen=True)
class LLMResult[T]:
    value: T
    record: CallRecord


class ClassifyRequest(StrictModel):
    """A ticket as the classifier sees it: text only, no address or customer details."""

    ticket_id: str = Field(min_length=1)
    subject: str | None = None
    body: str = Field(min_length=1)
    previous_error: str | None = Field(
        default=None,
        description="Validation error from a first attempt, put in front of the model on a retry.",
    )


class Passage(StrictModel):
    policy_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class DraftRequest(StrictModel):
    """Everything a draft may draw on. Nothing outside this is in the model's context."""

    ticket_id: str = Field(min_length=1)
    subject: str | None = None
    body: str = Field(min_length=1)
    order: dict[str, Any] | None = Field(
        default=None, description="The order record for this sender, or None if there is none."
    )
    order_snapshot_id: UUID | None = None
    passages: list[Passage] = Field(default_factory=list)
    policy_version: str = Field(min_length=1)
    sign_off: str = Field(min_length=1)
    failures: list[str] = Field(
        default_factory=list,
        description="Reasons a previous attempt was rejected, for the retry.",
    )


class JudgeRequest(StrictModel):
    reply_text: str = Field(min_length=1)
    citations: list[Citation] = Field(default_factory=list)
    order: dict[str, Any] | None = None
    passages: list[Passage] = Field(default_factory=list)


class DraftModelOutput(StrictModel):
    """What the model itself returns for a draft. The rest of `DraftOutput` (prompt version and
    hash, model, policy version) is filled in by the client, not left to the model."""

    reply_text: str = Field(min_length=1)
    citations: list[Citation] = Field(default_factory=list)
    proposed_action: ProposedAction = ProposedAction.NONE
    confidence: Probability


class LLMClient(ABC):
    @abstractmethod
    async def classify(self, request: ClassifyRequest) -> LLMResult[Classification]: ...

    @abstractmethod
    async def draft(self, request: DraftRequest) -> LLMResult[DraftOutput]: ...

    @abstractmethod
    async def judge(self, request: JudgeRequest) -> LLMResult[JudgeOutput]: ...

    @abstractmethod
    async def embed(self, texts: Sequence[str]) -> LLMResult[list[list[float]]]: ...

    async def aclose(self) -> None:  # noqa: B027 - optional hook, nothing to close by default
        """Release network resources. Safe to call more than once."""
