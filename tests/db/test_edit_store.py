"""What an owner's edit is checked against, read from a real drafted ticket, and the override
record in the audit log."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.grounding.edit import EditOverride, validate_edit
from services.api.pipeline.store import PipelineStore
from services.api.policy_source import loaded_passages
from tests.db.test_draft_step import Client, drafting_handler, drafts_of, ticket_for
from tests.db.test_pipeline_store import job_for
from tests.pipeline.test_draft_stage import SIGN_OFF, output

pytestmark = pytest.mark.anyio

WHEN = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


async def drafted(engine: AsyncEngine, case: int = 1):  # type: ignore[no-untyped-def]
    ticket_id = await ticket_for(engine, case)
    await (await drafting_handler(engine, Client([output()])))(job_for(ticket_id))
    return ticket_id


async def test_the_edit_context_is_the_drafts_own_sources(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine)
    edit = await PipelineStore(engine).edit_context(ticket_id)
    assert edit is not None
    [draft] = await drafts_of(engine, ticket_id)
    assert edit.draft_id == draft.draft_id
    assert edit.order is not None and edit.order["order_id"].startswith("RD-")
    assert edit.customer_email and edit.body
    assert edit.citations == []  # the scripted draft cites nothing


async def test_an_edit_is_validated_against_the_stored_order(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine)
    edit = await PipelineStore(engine).edit_context(ticket_id)
    assert edit is not None and edit.order is not None
    order_id = edit.order["order_id"]
    text = f"Your order {order_id} is here.\n\n{SIGN_OFF}"
    result = validate_edit(text, edit, sign_off=SIGN_OFF, policy_texts=loaded_passages())
    assert result.rule_ids == {"G-1"}  # the number is right but this draft cites nothing
    ok = validate_edit(
        f"Hello.\n\n{SIGN_OFF}", edit, sign_off=SIGN_OFF, policy_texts=loaded_passages()
    )
    assert ok.passed


async def test_a_ticket_with_no_draft_has_no_edit_context(engine: AsyncEngine) -> None:
    ticket_id = await ticket_for(engine, 1)  # prepared but not drafted
    assert await PipelineStore(engine).edit_context(ticket_id) is None


async def test_the_override_is_appended_to_the_audit_log(engine: AsyncEngine) -> None:
    ticket_id = await drafted(engine)
    store = PipelineStore(engine)
    edit = await store.edit_context(ticket_id)
    assert edit is not None
    override = EditOverride(
        reason="Customer asked for this wording",
        overridden_rules=["G-9", "G-3"],
        actor="owner@example.com",
        timestamp=WHEN,
    )
    await store.record_edit_override(ticket_id, edit.draft_id, override)

    async with engine.connect() as conn:
        last = (
            await conn.execute(
                text(
                    "SELECT event_type, actor, payload, occurred_at FROM audit_log"
                    " WHERE ticket_id = :id ORDER BY event_id DESC LIMIT 1"
                ),
                {"id": ticket_id},
            )
        ).one()
    assert last.event_type == "draft.edit_override" and last.actor == "owner@example.com"
    assert last.payload == {
        "draft_id": str(edit.draft_id),
        "reason": "Customer asked for this wording",
        "overridden_rules": ["G-3", "G-9"],
    }
    assert last.occurred_at == WHEN
