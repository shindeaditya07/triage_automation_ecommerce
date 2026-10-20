"""The `process_ticket` job handler: classify, then the pre-lookup escalation check. Later branches
add the steps after."""

import logging

from services.api.clock import Clock
from services.api.escalation.pre_lookup import decide
from services.api.jobs.worker import Handler, PermanentJobError
from services.api.llm.client import LLMClient
from services.api.models.enums import TicketStatus
from services.api.models.records import Job
from services.api.pipeline.classify import classify_ticket
from services.api.pipeline.store import PipelineStore

log = logging.getLogger("replydesk.pipeline")


def make_process_ticket_handler(store: PipelineStore, client: LLMClient, clock: Clock) -> Handler:
    """Each step reads the ticket's state and does only the step that state is waiting for, so a
    job that stopped half way picks up where it left off and one that ran twice does nothing."""

    async def handle(job: Job) -> None:
        if job.ticket_id is None:
            raise PermanentJobError("process_ticket job has no ticket")
        ticket = await store.get_text(job.ticket_id)
        if ticket is None:
            raise PermanentJobError("the ticket no longer exists")

        if ticket.status is TicketStatus.NEW:
            stage = await classify_ticket(
                client, str(ticket.ticket_id), ticket.subject, ticket.body
            )
            if stage.classification is None:
                reason = stage.escalation_reason or "classification_failed"
                await store.escalate(
                    ticket.ticket_id, "classification", reason, stage.calls, clock()
                )
                log.info("ticket escalated at classification: %s", reason)
                return
            await store.save_classification(
                ticket.ticket_id, stage.classification, stage.calls, stage.dropped_flags, clock()
            )
            ticket = await store.get_text(ticket.ticket_id) or ticket

        if ticket.status is TicketStatus.CLASSIFIED and ticket.classification is not None:
            decision = decide(ticket.classification, ticket.subject, ticket.body)
            if decision.escalate:
                await store.escalate(
                    ticket.ticket_id,
                    "pre_lookup",
                    decision.reason,
                    [],
                    clock(),
                    from_status=TicketStatus.CLASSIFIED,
                    flags=decision.flags,
                    detail={
                        "reasons": decision.reasons,
                        "backstop_rules": decision.backstop_rules,
                        "injection_signals": decision.injection_signals,
                    },
                )
                log.info("ticket escalated before lookup: %s", decision.reason)

    return handle
