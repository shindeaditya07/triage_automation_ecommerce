"""Where a classified ticket goes: the real rules, the real order lookup against the seeded mock
store, shared by the recorded run and the live run so both route a ticket exactly the same way."""

import importlib
from dataclasses import dataclass

import httpx

from evals import corpus
from evals.metrics import Prediction
from evals.schema import LabeledTicket, Outcome
from services.api.context.client import OrderStoreClient
from services.api.context.lookup import ContextLookup, look_up_context
from services.api.escalation import post_lookup
from services.api.escalation.pre_lookup import decide
from services.api.models.ticket import Classification

OUTCOMES = {
    post_lookup.Route.NO_DRAFT: Outcome.NO_DRAFT_ESCALATION,
    post_lookup.Route.SUGGESTION_ONLY: Outcome.SUGGESTION_ONLY,
    post_lookup.Route.DRAFT: Outcome.DRAFT,
}


async def look_up(ticket: LabeledTicket) -> ContextLookup:
    """The real lookup against the seeded mock store, served in-process at the frozen time."""
    mock_main = importlib.import_module("services.mock-store.main")
    transport = httpx.ASGITransport(app=mock_main.create_app(corpus.EVAL_NOW))
    async with httpx.AsyncClient(transport=transport, timeout=5) as http:
        return await look_up_context(
            OrderStoreClient(http, "http://mock-store.eval"),
            email=ticket.customer_email,
            subject=ticket.subject,
            body=corpus.resolve(ticket.body, ticket.scenario_case),
            order_id_hint=None,
        )


@dataclass(frozen=True)
class Routed:
    prediction: Prediction
    lookup: ContextLookup | None = None  # None: escalated before any lookup
    route: post_lookup.Route | None = None


async def route(ticket: LabeledTicket, classification: Classification) -> Routed:
    decision = decide(
        classification, ticket.subject, corpus.resolve(ticket.body, ticket.scenario_case)
    )
    if decision.escalate:
        return Routed(
            Prediction(
                intent=classification.intent,
                urgency=classification.urgency.value,
                outcome=Outcome.NO_DRAFT_ESCALATION,
                escalation_reasons=decision.reasons,
            )
        )
    lookup = await look_up(ticket)
    routing = post_lookup.decide(
        classification,
        lookup,
        thread_messages=ticket.thread_message_count,
        received_at=corpus.EVAL_NOW,
    )
    return Routed(
        Prediction(
            intent=classification.intent,
            urgency=classification.urgency.value,
            outcome=OUTCOMES[routing.route],
            escalation_reasons=routing.reasons,
        ),
        lookup,
        routing.route,
    )
