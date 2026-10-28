"""Retrieval recall: for tickets a draft is made for, are the policy passages the label cites among
the ones retrieved?

Each ticket is retrieved for with its labeled intent, so this isolates retrieval from the
classifier. Tone passages (TON-*) are left out of the target: the draft takes the sign-off from
settings, and TON-9.2 is always supplied. The run is done twice, once with the deterministic
passages only and once with the vector search added, to show what the search contributes.

Smoke mode embeds with the fake client's hashed bag-of-words vectors, so the vector half measures
the harness and the wiring, not a real embedding model.
"""

import asyncio
from collections.abc import Sequence

from pydantic import Field

from evals import corpus
from evals.metrics import Rate, rate
from evals.schema import LabeledTicket, Outcome
from scripts.policy_lib import POLICIES_PATH
from services.api.llm.client import LLMClient
from services.api.models.common import StrictModel
from services.api.retrieval.catalog import SUGGESTION_ONLY, deterministic_ids
from services.api.retrieval.index import MemoryPolicyIndex
from services.api.retrieval.retrieve import TOP_K, retrieve


class RetrievalMetrics(StrictModel):
    tickets: int
    passages: Rate = Field(description="Cited passages that were retrieved, intent + vector.")
    tickets_complete: Rate = Field(description="Tickets with every cited passage retrieved.")
    passages_by_intent_only: Rate = Field(description="The same without the vector search.")
    mean_retrieved: float
    missed: list[str] = Field(description="Tickets with at least one cited passage missing.")


def _targets(ticket: LabeledTicket) -> set[str]:
    return {pid for pid in ticket.policy_ids if not pid.startswith("TON-")}


async def measure(tickets: Sequence[LabeledTicket], llm: LLMClient) -> RetrievalMetrics:
    index = await MemoryPolicyIndex.build(llm, POLICIES_PATH.read_text(encoding="utf-8"))
    drafted = [
        t for t in tickets if t.outcome in (Outcome.DRAFT, Outcome.SUGGESTION_ONLY) and _targets(t)
    ]
    found = total = intent_found = complete = retrieved_count = 0
    missed = []
    for ticket in drafted:
        extra = SUGGESTION_ONLY if ticket.outcome is Outcome.SUGGESTION_ONLY else ()
        common = {
            "subject": ticket.subject,
            "body": corpus.resolve(ticket.body, ticket.scenario_case),
            "intent": ticket.intent,
            "extra_ids": extra,
        }
        full = await retrieve(index, llm, **common)  # type: ignore[arg-type]
        by_intent = set(deterministic_ids(ticket.intent, extra))
        target = _targets(ticket)
        hit = target & set(full.policy_ids)
        found += len(hit)
        total += len(target)
        intent_found += len(target & by_intent)
        retrieved_count += len(full.passages)
        if hit == target:
            complete += 1
        else:
            missed.append(ticket.id)
    return RetrievalMetrics(
        tickets=len(drafted),
        passages=rate(found, total),
        tickets_complete=rate(complete, len(drafted)),
        passages_by_intent_only=rate(intent_found, total),
        mean_retrieved=retrieved_count / len(drafted) if drafted else 0.0,
        missed=sorted(missed),
    )


def measure_sync(tickets: Sequence[LabeledTicket], llm: LLMClient) -> RetrievalMetrics:
    return asyncio.run(measure(tickets, llm))


__all__ = ["TOP_K", "RetrievalMetrics", "measure", "measure_sync"]
