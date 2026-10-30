"""The owner's review of a draft: save an edit, approve, reject; or reply to an escalated ticket.

The rules that keep a bad reply from being approved live here, not in the screen:
- an edit with grounding warnings is saved only with an override reason naming every rule that
  fired, and an approval of text with warnings needs that override on record;
- an escalated, suspected-injection or suggestion-only ticket is approved only after the owner
  says they viewed the original message, and a suggestion-only draft after a second confirmation;
- what is approved is the text that was checked, and a draft is decided once.
Approving records the decision and then the mock send (nothing leaves the system).
An escalated ticket has no draft: the owner writes the reply, it is checked like an edit (warnings
need a reason), and it is recorded as an approved draft of kind `owner` and mock-sent.
"""

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from services.api.auth.service import Owner
from services.api.clock import Clock
from services.api.grounding.config import GroundingConfig
from services.api.grounding.edit import EditOverride, OverrideError, check_override, validate_edit
from services.api.grounding.models import RULES, RuleFailure, ValidationResult
from services.api.grounding.sign_off import with_sign_off
from services.api.models.enums import ReviewStatus, TicketStatus
from services.api.review.auto import AUTO_APPROVER, auto_approve_block, confidence_of
from services.api.review.models import (
    ApproveRequest,
    OverrideInput,
    OwnerReplyRequest,
    RejectRequest,
    ReviewOutcome,
    SaveEditRequest,
)
from services.api.review.store import EscalatedState, NotOpen, ReviewState

log = logging.getLogger("replydesk.review")


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
        self,
        ticket_id: UUID,
        state: ReviewState,
        actor: str,
        now: datetime,
        automatic: dict[str, Any] | None = None,
    ) -> str: ...

    async def reject(
        self, ticket_id: UUID, state: ReviewState, reason: str, actor: str, now: datetime
    ) -> None: ...

    async def mock_send(self, ticket_id: UUID, now: datetime) -> bool: ...

    async def load_escalated(self, ticket_id: UUID) -> EscalatedState | None: ...

    async def owner_reply(
        self,
        ticket_id: UUID,
        state: EscalatedState,
        text: str,
        actor: str,
        now: datetime,
        override: EditOverride | None,
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

    @staticmethod
    def _override(
        result: ValidationResult, supplied: OverrideInput | None, owner: Owner, now: datetime
    ) -> EditOverride | None:
        """The override that lets text with grounding warnings through, or None when it has none.
        Raises `Invalid` (with the warnings) when warnings are not covered by a reason."""
        if supplied is not None:
            override = EditOverride(
                reason=supplied.reason,
                overridden_rules=supplied.overridden_rules,
                actor=owner.email,
                timestamp=now,
            )
            try:
                check_override(result, override)
            except OverrideError as exc:
                raise Invalid(str(exc), result.failures) from None
            return override
        if not result.passed:
            raise Invalid(
                "this text has grounding warnings; give a reason to save it anyway",
                result.failures,
            )
        return None

    async def save_edit(
        self, ticket_id: UUID, request: SaveEditRequest, owner: Owner
    ) -> ReviewOutcome:
        state = await self._open(ticket_id)
        result = self._check(request.reply_text, state)
        now = self._clock()
        override = self._override(result, request.override, owner, now)
        try:
            await self._store.save_edit(
                ticket_id, state, request.reply_text, owner.email, now, override
            )
        except NotOpen:
            raise Conflict(
                "this draft has already been decided, or the ticket has moved on"
            ) from None
        return self._outcome(ticket_id, state, final_text=request.reply_text)

    async def owner_reply(
        self, ticket_id: UUID, request: OwnerReplyRequest, owner: Owner
    ) -> ReviewOutcome:
        """The owner answers an escalated ticket themselves. Nothing leaves the system."""
        state = await self._store.load_escalated(ticket_id)
        if state is None:
            raise NoDraft
        if state.ticket_status is not TicketStatus.ESCALATED:
            raise Conflict("only an escalated ticket takes an owner reply")
        if not request.original_viewed:
            raise Conflict("view the customer's original message before replying")
        # The store's sign-off is added when missing, as for a model draft, so the owner does not
        # have to type it or override the sign-off rule on every reply.
        text = with_sign_off(request.reply_text, self._sign_off)
        result = validate_edit(
            text,
            state.edit,
            sign_off=self._sign_off,
            policy_texts=self._policy_texts,
            config=self._config,
        )
        # The owner's text carries no citations, so a value that is right but uncited is no warning.
        result = ValidationResult(failures=[f for f in result.failures if not f.citation_missing])
        now = self._clock()
        override = self._override(result, request.override, owner, now)
        try:
            await self._store.owner_reply(ticket_id, state, text, owner.email, now, override)
        except NotOpen:
            raise Conflict("this ticket was answered or changed while you replied") from None
        sent = await self._send(ticket_id)
        return ReviewOutcome(
            ticket_id=ticket_id,
            draft_id=state.draft_id,
            ticket_status=TicketStatus.SENT if sent else TicketStatus.APPROVED,
            review_status=ReviewStatus.APPROVED,
            final_text=text,
        )

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
        # The decision is recorded above. If recording the send fails, the ticket stays approved
        # and `send` below finishes it; the approval is never lost.
        sent = await self._send(ticket_id)
        return self._outcome(
            ticket_id,
            state,
            ticket_status=TicketStatus.SENT if sent else TicketStatus.APPROVED,
            review_status=ReviewStatus.APPROVED,
            final_text=text,
        )

    async def auto_approve(self, ticket_id: UUID, threshold: float) -> ReviewOutcome | None:
        """Approve and mock-send a fresh draft with no owner when it is clean and confident (see
        `auto_approve_block`); None when it must wait for the owner. The text is checked again
        with the same grounding rules an owner's approval uses, so a draft that would not pass
        there does not pass here."""
        state = await self._store.load(ticket_id)
        if state is None:
            return None
        reason = auto_approve_block(state, threshold)
        if reason is None and not self._check(state.effective_text, state).passed:
            reason = "the text has grounding warnings"
        if reason is not None:
            log.info("not auto-approved: %s", reason)
            return None
        try:
            text = await self._store.approve(
                ticket_id,
                state,
                AUTO_APPROVER,
                self._clock(),
                automatic={
                    "classifier_confidence": state.classifier_confidence,
                    "draft_confidence": state.draft_confidence,
                    "confidence": confidence_of(state),
                    "threshold": threshold,
                },
            )
        except NotOpen:
            return None
        sent = await self._send(ticket_id)
        return self._outcome(
            ticket_id,
            state,
            ticket_status=TicketStatus.SENT if sent else TicketStatus.APPROVED,
            review_status=ReviewStatus.APPROVED,
            final_text=text,
        )

    async def _send(self, ticket_id: UUID) -> bool:
        try:
            return await self._store.mock_send(ticket_id, self._clock())
        except Exception:
            log.exception("recording the mock send failed")
            return False

    async def send(self, ticket_id: UUID) -> ReviewOutcome:
        """Finish an approved ticket whose send was not recorded. Safe to call again."""
        state = await self._store.load(ticket_id)
        if state is None:
            raise NoDraft
        if state.review_status is not ReviewStatus.APPROVED:
            raise Conflict("only an approved draft can be sent")
        if state.ticket_status is TicketStatus.APPROVED and not await self._send(ticket_id):
            raise Conflict("the send could not be recorded; try again")
        return self._outcome(
            ticket_id,
            state,
            ticket_status=TicketStatus.SENT,
            review_status=ReviewStatus.APPROVED,
            final_text=state.final_text,
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
