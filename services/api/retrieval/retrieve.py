"""Policy retrieval for a ticket: the passages the draft may draw on.

The result is the passages that must always be there for the ticket's intent
(`catalog.deterministic_ids`) followed by the closest passages by meaning, so the answer never
rests on the vector search alone. Every ID is a passage of the loaded policy set; if one the
catalog names is missing from the index, retrieval raises instead of drafting without it
(docs/AGENTS.md: fail closed).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from services.api.llm.client import CallRecord, LLMClient
from services.api.models.enums import Intent
from services.api.retrieval.catalog import deterministic_ids, unknown_policy_ids
from services.api.retrieval.index import PolicyIndex

TOP_K = 4  # closest passages by meaning, beyond the ones the intent requires
MAX_QUERY_CHARS = 4000  # an embedding input is a few paragraphs; the ticket is already masked


class RetrievalError(Exception):
    """The policy passages a draft needs could not be assembled. The ticket goes to the owner."""


class Source(StrEnum):
    INTENT = "intent"  # always retrieved for this intent
    VECTOR = "vector"  # close in meaning to the message


@dataclass(frozen=True)
class RetrievedPassage:
    policy_id: str
    text: str
    source: Source
    score: float | None  # cosine similarity for a vector hit, None for the others


@dataclass(frozen=True)
class Retrieval:
    policy_version: str
    passages: list[RetrievedPassage]
    call: CallRecord

    @property
    def policy_ids(self) -> list[str]:
        return [passage.policy_id for passage in self.passages]


def query_text(subject: str | None, body: str) -> str:
    return "\n".join(part for part in (subject, body) if part)[:MAX_QUERY_CHARS]


async def retrieve(
    index: PolicyIndex,
    llm: LLMClient,
    *,
    subject: str | None,
    body: str,
    intent: Intent,
    extra_ids: Sequence[str] = (),
    top_k: int = TOP_K,
) -> Retrieval:
    """`extra_ids` are passages the caller needs on top of the intent's, for example ESC-8.3 for a
    suggestion-only draft. A model failure raises its `LLMError`, which the caller turns into an
    escalation."""
    required = deterministic_ids(intent, extra_ids)
    unknown = unknown_policy_ids(required)
    if unknown:
        raise RetrievalError(f"not policy passages: {', '.join(unknown)}")

    embedded = await llm.embed([query_text(subject, body)])
    vector = embedded.value[0]
    hits = await index.nearest(vector, top_k + len(required), embedded.record.model)
    nearby = [hit for hit in hits if hit.policy_id not in required][:top_k]

    texts = await index.texts([*required, *(hit.policy_id for hit in nearby)])
    missing = [policy_id for policy_id in required if policy_id not in texts]
    if missing:
        raise RetrievalError(
            f"policy index (version {index.version}) is missing {', '.join(missing)}"
        )

    passages = [RetrievedPassage(pid, texts[pid], Source.INTENT, None) for pid in required]
    passages += [
        RetrievedPassage(hit.policy_id, texts[hit.policy_id], Source.VECTOR, hit.score)
        for hit in nearby
        if hit.policy_id in texts
    ]
    return Retrieval(index.version, passages, embedded.record)
