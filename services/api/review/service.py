"""The owner's review of a draft: save an edit, approve, reject.

The rules that keep a bad reply from being approved live here, not in the screen:
- an edit with grounding warnings is saved only with an override reason naming every rule that
  fired, and an approval of text with warnings needs that override on record;
- an escalated, suspected-injection or suggestion-only ticket is approved only after the owner
  says they viewed the original message, and a suggestion-only draft after a second confirmation;
- what is approved is the text that was checked, and a draft is decided once.
Approving records the decision; nothing is sent to the customer here.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol
from uuid import UUID

from services.api.auth.service import Owner
from services.api.clock import Clock
from services.api.grounding.config import GroundingConfig
from services.api.grounding.edit import EditOverride, OverrideError, check_override, validate_edit
from services.api.grounding.models import RULES, RuleFailure
from services.api.models.enums import ReviewStatus, TicketStatus
from services.api.review.models import (
    ApproveRequest,
    RejectRequest,
    ReviewOutcome,
    SaveEditRequest,
)
from services.api.review.store import NotOpen, ReviewState


class ReviewStorePort(Protocol):
    async def load(self, ticket_id: UUID) -> ReviewState | None: ...

    async def override_rules(self, draft_id: UUID) -> set[str]: ...

    async def save_edit(
        self,
        ticket_id: UUID,
        state: ReviewState,
        text: str,
        actor: str,
        now: datetime,
        override: EditOverride | None,
    ) -> None: ...

    async def approve(
        self, ticket_id: UUID, state: ReviewState, actor: str, now: datetime
    ) -> str: ...

    async def reject(
        self, ticket_id: UUID, state: ReviewState, reason: str, actor: str, now: datetime
    ) -> None: ...


class NoDraft(Exception):
    """The ticket does not exist or has no draft."""


class Conflict(Exception):
    """The request is valid but not allowed in the draft's current state."""


class Invalid(Exception):
    """The request cannot be accepted as sent; `failures` are the grounding warnings, if any."""

    def __init__(self, message: str, failures: list[RuleFailure] | None = None) -> None:
        super().__init__(message)
        self.failures = failures or []


class ReviewService:
    def __init__(
        self,
        store: ReviewStorePort,
        clock: Clock,
        sign_off: str,
        policy_texts: Mapping[str, str],
        config: GroundingConfig | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._sign_off = sign_off
        self._policy_texts = policy_texts
        self._config = config

    async def _open(self, ticket_id: UUID) -> ReviewState:
        state = await self._store.load(ticket_id)
        if state is None:
            raise NoDraft
        if not state.open:
            raise Conflict("this draft has already been decided, or the ticket has moved on")
        return state

    def _check(self, text: str, state: ReviewState):  # type: ignore[no-untyped-def]
        return validate_edit(
            text,
            state.edit,
            sign_off=self._sign_off,
            policy_texts=self._policy_texts,
            config=self._config,
        )

    def _outcome(self, ticket_id: UUID, state: ReviewState, **changes: object) -> ReviewOutcome:
        values = {
            "ticket_id": ticket_id,
            "draft_id": state.draft_id,
            "ticket_status": TicketStatus.DRAFTED,
            "review_status": ReviewStatus.PENDING,
            "final_text": state.final_text,
        }
        return ReviewOutcome(**{**values, **changes})  # type: ignore[arg-type]

    async def save_edit(
        self, ticket_id: UUID, request: SaveEditRequest, owner: Owner
    ) -> ReviewOutcome:
        state = await self._open(ticket_id)
        result = self._check(request.reply_text, state)
        override: EditOverride | None = None
        now = self._clock()
        if request.override is not None:
            override = EditOverride(
                reason=request.override.reason,
                overridden_rules=request.override.overridden_rules,
                actor=owner.email,
                timestamp=now,
            )
            try:
                check_override(result, override)
            except OverrideError as exc:
                raise Invalid(str(exc), result.failures) from None
        elif not result.passed:
            raise Invalid(
                "this text has grounding warnings; give a reason to save it anyway",
                result.failures,
            )
        try:
            await self._store.save_edit(
                ticket_id, state, request.reply_text, owner.email, now, override
            )
        except NotOpen:
            raise Conflict(
                "this draft has already been decided, or the ticket has moved on"
            ) from None
        return self._outcome(ticket_id, state, final_text=request.reply_text)

    async def approve(
        self, ticket_id: UUID, request: ApproveRequest, owner: Owner
    ) -> ReviewOutcome:
        state = await self._open(ticket_id)
        if state.needs_original_viewed and not request.original_viewed:
            raise Conflict("view the customer's original message before approving this ticket")
        if state.suggestion_only and not request.confirm_suggestion_only:
            raise Conflict("this is a suggestion-only draft; confirm that you want to approve it")
        result = self._check(state.effective_text, state)
        if not result.passed:
            uncovered = result.rule_ids - await self._store.override_rules(state.draft_id)
            if uncovered:
                raise Conflict(
                    "the text has grounding warnings that were not overridden: "
                    + ", ".join(sorted(uncovered, key=RULES.index))
                )
        try:
            text = await self._store.approve(ticket_id, state, owner.email, self._clock())
        except NotOpen:
            raise Conflict("this draft changed or was decided while you approved it") from None
        return self._outcome(
            ticket_id,
            state,
            ticket_status=TicketStatus.APPROVED,
            review_status=ReviewStatus.APPROVED,
            final_text=text,
        )

    async def reject(self, ticket_id: UUID, request: RejectRequest, owner: Owner) -> ReviewOutcome:
        state = await self._open(ticket_id)
        try:
            await self._store.reject(ticket_id, state, request.reason, owner.email, self._clock())
        except NotOpen:
            raise Conflict("this draft changed or was decided while you rejected it") from None
        return self._outcome(
            ticket_id,
            state,
            ticket_status=TicketStatus.REJECTED,
            review_status=ReviewStatus.REJECTED,
        )
