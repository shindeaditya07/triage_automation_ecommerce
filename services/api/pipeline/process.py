"""The `process_ticket` job handler. For now it classifies; later branches add the steps after.

A ticket that is no longer `new` is skipped, so a job run twice (a slow worker whose lock expired)
does not classify again.
"""

import logging

from services.api.clock import Clock
from services.api.jobs.worker import Handler, PermanentJobError
from services.api.llm.client import LLMClient
from services.api.models.enums import TicketStatus
from services.api.models.records import Job
from services.api.pipeline.classify import classify_ticket
from services.api.pipeline.store import PipelineStore

log = logging.getLogger("replydesk.pipeline")


def make_process_ticket_handler(store: PipelineStore, client: LLMClient, clock: Clock) -> Handler:
    async def handle(job: Job) -> None:
        if job.ticket_id is None:
            raise PermanentJobError("process_ticket job has no ticket")
        ticket = await store.get_text(job.ticket_id)
        if ticket is None:
            raise PermanentJobError("the ticket no longer exists")
        if ticket.status is not TicketStatus.NEW:
            return
        stage = await classify_ticket(client, str(ticket.ticket_id), ticket.subject, ticket.body)
        if stage.classification is None:
            reason = stage.escalation_reason or "classification_failed"
            await store.escalate(ticket.ticket_id, "classification", reason, stage.calls, clock())
            log.info("ticket escalated at classification: %s", reason)
            return
        await store.save_classification(
            ticket.ticket_id, stage.classification, stage.calls, stage.dropped_flags, clock()
        )

    return handle
