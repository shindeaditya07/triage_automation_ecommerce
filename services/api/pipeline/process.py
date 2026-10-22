"""The `process_ticket` job handler: classify, check for pre-lookup escalation, look up the order,
check for post-lookup escalation, draft the reply and check it is grounded. Later branches add the
steps after."""

import logging
from dataclasses import asdict

from services.api.clock import Clock
from services.api.context.client import OrderStoreClient, StoreContractError
from services.api.context.lookup import look_up_context
from services.api.escalation import post_lookup
from services.api.escalation.pre_lookup import decide
from services.api.jobs.worker import Handler, PermanentJobError
from services.api.llm.client import LLMClient
from services.api.models.enums import TicketStatus
from services.api.models.records import Job
from services.api.pipeline.classify import classify_ticket
from services.api.pipeline.draft import DraftingConfig, draft_ticket
from services.api.pipeline.store import PipelineStore

log = logging.getLogger("replydesk.pipeline")


def make_process_ticket_handler(
    store: PipelineStore,
    client: LLMClient,
    orders: OrderStoreClient,
    clock: Clock,
    drafting: DraftingConfig | None = None,
) -> Handler:
    """Each step reads the ticket's state and does only the step that state is waiting for, so a
    job that stopped half way picks up where it left off and one that ran twice does nothing.
    Without `drafting` the handler stops after routing (tests of the earlier steps)."""

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
                    ticket.ticket_id,
                    "classification",
                    reason,
                    stage.calls,
                    clock(),
                    detail={"error": stage.error} if stage.error else None,
                )
                log.info("ticket escalated at classification: %s %s", reason, stage.error or "")
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
                return

            if not ticket.lookup_done:
                try:
                    lookup = await look_up_context(
                        orders,
                        email=ticket.customer_email,
                        subject=ticket.subject,
                        body=ticket.body,
                        order_id_hint=ticket.order_id_hint,
                    )
                except StoreContractError as exc:
                    raise PermanentJobError(str(exc)) from None
                # StoreUnavailable is not caught: the job is retried later.
                await store.save_order_lookup(ticket.ticket_id, lookup, clock())
                log.info("order lookup: %s", lookup.result.value)

            suggestion_only = ticket.escalation_reason is not None  # routed on an earlier run
            if not suggestion_only:
                recorded = await store.load_lookup(ticket.ticket_id)
                if recorded is None:
                    raise PermanentJobError("the order lookup is missing")
                routing = post_lookup.decide(
                    ticket.classification,
                    recorded,
                    thread_messages=await store.count_thread_messages(ticket.ticket_id),
                    received_at=ticket.received_at,
                )
                if routing.route is post_lookup.Route.NO_DRAFT:
                    await store.escalate(
                        ticket.ticket_id,
                        "post_lookup",
                        routing.reason,
                        [],
                        clock(),
                        from_status=TicketStatus.CLASSIFIED,
                        flags=routing.flags,
                        detail={"reasons": routing.reasons},
                    )
                    log.info("ticket escalated after lookup: %s", routing.reason)
                    return
                if routing.route is post_lookup.Route.SUGGESTION_ONLY:
                    await store.mark_suggestion_only(
                        ticket.ticket_id, routing.reason, routing.triggers, routing.flags, clock()
                    )
                    log.info("ticket routed suggestion-only: %s", ",".join(routing.triggers))
                    suggestion_only = True

            if drafting is None:
                return
            recorded = await store.load_lookup(ticket.ticket_id)
            if recorded is None:
                raise PermanentJobError("the order lookup is missing")
            written = await draft_ticket(
                client,
                drafting,
                ticket,
                recorded,
                snapshot_id=await store.snapshot_id(ticket.ticket_id),
                suggestion_only=suggestion_only,
            )
            if written.draft is None:
                reason = written.escalation_reason or "draft_failed"
                await store.escalate(
                    ticket.ticket_id,
                    "draft",
                    reason,
                    written.calls,
                    clock(),
                    from_status=TicketStatus.CLASSIFIED,
                    detail={"error": written.error} if written.error else None,
                )
                log.info("ticket escalated at drafting: %s %s", reason, written.error or "")
                return
            await store.save_draft(
                ticket.ticket_id,
                written.draft,
                passage_ids=written.passage_ids,
                calls=written.calls,
                suggestion_only=suggestion_only,
                now=clock(),
                grounding_result=written.grounding.audit_payload() if written.grounding else None,
                kind=written.kind,
                attempt=written.attempt,
                failed_attempts=[asdict(f) for f in written.failed_attempts],
            )
            log.info(
                "draft stored (kind=%s, attempt=%d, suggestion_only=%s)",
                written.kind.value,
                written.attempt,
                suggestion_only,
            )

    return handle
