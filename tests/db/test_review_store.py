"""The owner's decisions on a real drafted ticket: one transaction each, with its audit event."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.grounding.edit import EditOverride
from services.api.models.enums import ReviewStatus, TicketStatus
from services.api.review.store import NotOpen, ReviewStore, edit_diff
from tests.db.test_draft_step import Client, drafting_handler, ticket_for
from tests.db.test_pipeline_store import job_for
from tests.pipeline.test_draft_stage import output

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
OWNER = "owner@example.com"


async def drafted(engine: AsyncEngine, case: int = 1):  # type: ignore[no-untyped-def]
    ticket_id = await ticket_for(engine, case)
    await (await drafting_handler(engine, Client([output()])))(job_for(ticket_id))
    return ticket_id


async def events(engine: AsyncEngine, ticket_id) -> list[tuple[str, str, dict]]:  # type: ignore[no-untyped-def]
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT event_type, actor, payload FROM audit_log WHERE ticket_id = :t AND "
                "(event_type LIKE 'draft.edit%' OR event_type IN "
                "('ticket.approved', 'ticket.rejected')) ORDER BY event_id"
            ),
            {"t": ticket_id},
        )
        return [(r.event_type, r.actor, r.payload) for r in rows]


async def statuses(engine: AsyncEngine, ticket_id) -> tuple[str, str, str | None]:  # type: ignore[no-untyped-def]
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT t.status, d.review_status, d.final_text FROM tickets t "
                    "JOIN drafts d USING (ticket_id) WHERE t.ticket_id = :t"
                ),
                {"t": ticket_id},
            )
        ).one()
        return row.status, row.review_status, row.final_text


async def test_a_drafted_ticket_loads_as_open(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine)
    state = await ReviewStore(engine).load(ticket_id)
    assert state is not None and state.open
    assert state.effective_text == state.reply_text and state.final_text is None
    assert state.edit.order is not None


async def test_a_ticket_without_a_draft_does_not_load(engine: AsyncEngine) -> None:
    assert await ReviewStore(engine).load(await ticket_for(engine, 1)) is None


async def test_approving_moves_the_ticket_and_the_draft_and_records_it(
    engine: AsyncEngine,
) -> None:
    ticket_id = await drafted(engine)
    store = ReviewStore(engine)
    state = await store.load(ticket_id)
    assert state is not None
    approved = await store.approve(ticket_id, state, OWNER, NOW)
    assert approved == state.reply_text
    assert await statuses(engine, ticket_id) == ("approved", "approved", state.reply_text)
    [(kind, actor, payload)] = [
        e for e in await events(engine, ticket_id) if e[0] == "ticket.approved"
    ]
    assert (kind, actor) == ("ticket.approved", OWNER)
    assert payload["edited"] is False and payload["diff"] == ""
    reloaded = await store.load(ticket_id)
    assert reloaded is not None and not reloaded.open
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT reviewed_by, reviewed_at FROM drafts WHERE ticket_id = :t"),
                {"t": ticket_id},
            )
        ).one()
    assert (row.reviewed_by, row.reviewed_at) == (OWNER, NOW)


async def test_a_draft_cannot_be_decided_twice(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine)
    store = ReviewStore(engine)
    state = await store.load(ticket_id)
    assert state is not None
    await store.approve(ticket_id, state, OWNER, NOW)
    with pytest.raises(NotOpen):
        await store.approve(ticket_id, state, OWNER, NOW)
    with pytest.raises(NotOpen):
        await store.reject(ticket_id, state, "changed my mind", OWNER, NOW)
    with pytest.raises(NotOpen):
        await store.save_edit(ticket_id, state, "new text", OWNER, NOW, None)
    assert (await statuses(engine, ticket_id))[:2] == ("approved", "approved")


async def test_what_is_approved_is_the_text_that_was_checked(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine)
    store = ReviewStore(engine)
    checked = await store.load(ticket_id)
    assert checked is not None
    # another tab saves an edit after this state was read and checked
    await store.save_edit(ticket_id, checked, "A different reply", OWNER, NOW, None)
    with pytest.raises(NotOpen):
        await store.approve(ticket_id, checked, OWNER, NOW)
    assert (await statuses(engine, ticket_id))[:2] == ("drafted", "pending")


async def test_an_edit_is_saved_with_its_diff_and_the_original_is_kept(
    engine: AsyncEngine,
) -> None:
    ticket_id = await drafted(engine)
    store = ReviewStore(engine)
    state = await store.load(ticket_id)
    assert state is not None
    override = EditOverride(
        reason="I will handle the refund", overridden_rules=["G-3"], actor=OWNER, timestamp=NOW
    )
    await store.save_edit(ticket_id, state, "Edited reply text", OWNER, NOW, override)
    after = await store.load(ticket_id)
    assert after is not None and after.final_text == "Edited reply text"
    assert after.reply_text == state.reply_text  # the model's draft is never overwritten
    assert await store.override_rules(state.draft_id) == {"G-3"}
    kinds = [e[0] for e in await events(engine, ticket_id)]
    assert kinds[-2:] == ["draft.edited", "draft.edit_override"]
    approved = await store.approve(ticket_id, after, OWNER, NOW)
    assert approved == "Edited reply text"
    [(_, _, payload)] = [e for e in await events(engine, ticket_id) if e[0] == "ticket.approved"]
    assert payload["edited"] is True and "Edited reply text" in payload["diff"]


async def test_rejecting_records_the_reason(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine)
    store = ReviewStore(engine)
    state = await store.load(ticket_id)
    assert state is not None
    await store.reject(ticket_id, state, "the tone is wrong", OWNER, NOW)
    status, review, final = await statuses(engine, ticket_id)
    assert (status, review, final) == (
        TicketStatus.REJECTED.value,
        ReviewStatus.REJECTED.value,
        None,
    )
    [(_, actor, payload)] = [
        e for e in await events(engine, ticket_id) if e[0] == "ticket.rejected"
    ]
    assert actor == OWNER and payload["reason"] == "the tone is wrong"


async def test_a_ticket_that_moved_on_is_not_open(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine)
    store = ReviewStore(engine)
    state = await store.load(ticket_id)
    assert state is not None
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tickets SET status = 'escalated', escalation_reason = 'x' "
                "WHERE ticket_id = :t"
            ),
            {"t": ticket_id},
        )
    with pytest.raises(NotOpen):
        await store.approve(ticket_id, replace(state), OWNER, NOW)


def test_the_diff_shows_what_changed() -> None:
    diff = edit_diff("Hello\nOld line\nBye", "Hello\nNew line\nBye")
    assert "-Old line" in diff and "+New line" in diff
    assert edit_diff("same", "same") == ""
    assert len(edit_diff("a\n" * 50_000, "b\n" * 50_000)) <= 20_000


async def test_the_mock_send_marks_the_ticket_sent_once(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine)
    store = ReviewStore(engine)
    state = await store.load(ticket_id)
    assert state is not None
    assert await store.mock_send(ticket_id, NOW) is False  # not approved yet
    await store.approve(ticket_id, state, OWNER, NOW)
    assert await store.mock_send(ticket_id, NOW) is True
    assert await store.mock_send(ticket_id, NOW) is False  # a retry does nothing
    assert (await statuses(engine, ticket_id))[:2] == ("sent", "approved")
    async with engine.connect() as conn:
        sent = (
            await conn.execute(
                text("SELECT sent_at FROM drafts WHERE ticket_id = :t"), {"t": ticket_id}
            )
        ).scalar_one()
        rows = (
            await conn.execute(
                text(
                    "SELECT actor, payload FROM audit_log WHERE ticket_id = :t "
                    "AND event_type = 'ticket.mock_sent'"
                ),
                {"t": ticket_id},
            )
        ).all()
    assert sent == NOW
    [row] = rows
    assert row.actor == "system" and row.payload["approved_by"] == OWNER
    assert row.payload["final_text_chars"] == len(state.reply_text)


async def test_a_rejected_draft_is_never_sent(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine)
    store = ReviewStore(engine)
    state = await store.load(ticket_id)
    assert state is not None
    await store.reject(ticket_id, state, "not good enough", OWNER, NOW)
    assert await store.mock_send(ticket_id, NOW) is False
    assert (await statuses(engine, ticket_id))[0] == "rejected"


async def escalate(engine: AsyncEngine, ticket_id) -> None:  # type: ignore[no-untyped-def]
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tickets SET status = 'escalated', escalation_reason = 'legal_threat' "
                "WHERE ticket_id = :t"
            ),
            {"t": ticket_id},
        )


async def test_an_owner_reply_is_an_approved_owner_draft_and_is_mock_sent(
    engine: AsyncEngine,
) -> None:
    ticket_id = await drafted(engine)
    await escalate(engine, ticket_id)
    store = ReviewStore(engine)
    state = await store.load_escalated(ticket_id)
    assert state is not None and state.ticket_status is TicketStatus.ESCALATED

    await store.owner_reply(ticket_id, state, "My own reply", OWNER, NOW, None)
    assert await store.mock_send(ticket_id, NOW) is True

    async with engine.connect() as conn:
        draft = (
            await conn.execute(
                text(
                    "SELECT draft_id, attempt, kind, review_status, final_text, reviewed_by, "
                    "sent_at FROM drafts WHERE ticket_id = :t ORDER BY attempt DESC LIMIT 1"
                ),
                {"t": ticket_id},
            )
        ).one()
        status = (
            await conn.execute(
                text("SELECT status FROM tickets WHERE ticket_id = :t"), {"t": ticket_id}
            )
        ).scalar_one()
        kinds = [
            row.event_type
            for row in await conn.execute(
                text("SELECT event_type FROM audit_log WHERE ticket_id = :t ORDER BY event_id"),
                {"t": ticket_id},
            )
        ]
    assert draft.draft_id == state.draft_id
    assert (draft.attempt, draft.kind, draft.review_status) == (2, "owner", "approved")
    assert (draft.final_text, draft.reviewed_by) == ("My own reply", OWNER)
    assert draft.sent_at is not None
    assert status == "sent"
    assert kinds[-2:] == ["ticket.owner_replied", "ticket.mock_sent"]


async def test_an_escalated_ticket_is_answered_once(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine)
    await escalate(engine, ticket_id)
    store = ReviewStore(engine)
    state = await store.load_escalated(ticket_id)
    assert state is not None
    await store.owner_reply(ticket_id, state, "First", OWNER, NOW, None)
    with pytest.raises(NotOpen):
        await store.owner_reply(ticket_id, state, "Second", OWNER, NOW, None)


async def test_a_ticket_that_is_not_escalated_takes_no_owner_reply(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine)
    store = ReviewStore(engine)
    state = await store.load_escalated(ticket_id)
    assert state is not None and state.ticket_status is TicketStatus.DRAFTED
    with pytest.raises(NotOpen):
        await store.owner_reply(ticket_id, state, "Too early", OWNER, NOW, None)
    assert await store.load_escalated(uuid4()) is None
