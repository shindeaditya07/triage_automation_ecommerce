"""The policy index: passage text and vectors, kept in `policy_embeddings`.

`PolicyIndex` is what retrieval needs from an index, so it can run against Postgres (pgvector) or,
in tests and the eval, against an in-memory index with the same behaviour. `build_index` embeds
`docs/policies.md` (policies and FAQ alike, they are all passages) and stores one row per policy
ID and policy version.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.db.tables import policy_embeddings as table
from services.api.llm.client import CallRecord, LLMClient
from services.api.policy_source import parse_passages, policies_version

EMBED_BATCH = 64


@dataclass(frozen=True)
class Hit:
    policy_id: str
    score: float  # cosine similarity, 1 is identical


class PolicyIndex(Protocol):
    version: str

    async def nearest(self, vector: Sequence[float], limit: int, model: str) -> list[Hit]:
        """The closest passages of this policy version embedded with `model`, best first."""
        ...

    async def texts(self, policy_ids: Sequence[str]) -> dict[str, str]:
        """Passage text by ID. IDs the index does not hold are left out."""
        ...


class PgPolicyIndex:
    def __init__(self, engine: AsyncEngine, version: str) -> None:
        self._engine = engine
        self.version = version

    async def nearest(self, vector: Sequence[float], limit: int, model: str) -> list[Hit]:
        distance = table.c.embedding.cosine_distance(list(vector))
        query = (
            select(table.c.policy_id, distance.label("distance"))
            .where(table.c.policy_version == self.version, table.c.embed_model == model)
            .order_by(distance, table.c.policy_id)
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(query)).all()
        return [Hit(row.policy_id, 1.0 - float(row.distance)) for row in rows]

    async def texts(self, policy_ids: Sequence[str]) -> dict[str, str]:
        query = select(table.c.policy_id, table.c.passage_text).where(
            table.c.policy_version == self.version, table.c.policy_id.in_(list(policy_ids))
        )
        async with self._engine.connect() as conn:
            return {row.policy_id: row.passage_text for row in await conn.execute(query)}


@dataclass(frozen=True)
class BuildResult:
    version: str
    embedded: int
    unchanged: int
    model: str | None  # the model that embedded them; None when nothing needed embedding
    calls: list[CallRecord]


async def build_index(
    engine: AsyncEngine, llm: LLMClient, *, policies_text: str, embed_model: str
) -> BuildResult:
    """Make the index hold every passage of `policies_text` at its version, embedded with
    `embed_model`. A passage whose stored text and model already match is left alone, so running
    this again changes nothing; one whose text or model differs is re-embedded in place."""
    version = policies_version(policies_text)
    passages = parse_passages(policies_text)

    async with engine.connect() as conn:
        stored = {
            row.policy_id: (row.passage_text, row.embed_model)
            for row in await conn.execute(
                select(table.c.policy_id, table.c.passage_text, table.c.embed_model).where(
                    table.c.policy_version == version
                )
            )
        }
    todo = {
        policy_id: text
        for policy_id, text in passages.items()
        if stored.get(policy_id) != (text, embed_model)
    }

    calls: list[CallRecord] = []
    used_model: str | None = None
    ids = list(todo)
    for start in range(0, len(ids), EMBED_BATCH):
        batch = ids[start : start + EMBED_BATCH]
        result = await llm.embed([todo[policy_id] for policy_id in batch])
        if result.record.model != embed_model:
            raise ValueError(
                f"embedded with {result.record.model!r}, expected {embed_model!r}: the index must "
                "carry the name the search will use"
            )
        calls.append(result.record)
        used_model = result.record.model
        rows = [
            {
                "policy_id": policy_id,
                "policy_version": version,
                "passage_text": todo[policy_id],
                "embed_model": result.record.model,
                "embedding": vector,
            }
            for policy_id, vector in zip(batch, result.value, strict=True)
        ]
        statement = insert(table).values(rows)
        statement = statement.on_conflict_do_update(
            constraint="uq_policy_embeddings_policy_id_version",
            set_={
                "passage_text": statement.excluded.passage_text,
                "embed_model": statement.excluded.embed_model,
                "embedding": statement.excluded.embedding,
            },
        )
        async with engine.begin() as conn:
            await conn.execute(statement)

    return BuildResult(version, len(todo), len(passages) - len(todo), used_model, calls)


class MemoryPolicyIndex:
    """The same index in memory: for tests and the eval, which have no database."""

    def __init__(
        self, version: str, passages: Mapping[str, str], vectors: Mapping[str, Sequence[float]]
    ) -> None:
        self.version = version
        self._passages = dict(passages)
        self._vectors = {policy_id: list(vector) for policy_id, vector in vectors.items()}

    @classmethod
    async def build(cls, llm: LLMClient, policies_text: str) -> "MemoryPolicyIndex":
        passages = parse_passages(policies_text)
        result = await llm.embed(list(passages.values()))
        vectors = dict(zip(passages, result.value, strict=True))
        return cls(policies_version(policies_text), passages, vectors)

    async def nearest(self, vector: Sequence[float], limit: int, model: str) -> list[Hit]:
        scored = [
            Hit(policy_id, sum(a * b for a, b in zip(vector, other, strict=True)))
            for policy_id, other in self._vectors.items()
        ]
        return sorted(scored, key=lambda hit: (-hit.score, hit.policy_id))[:limit]

    async def texts(self, policy_ids: Sequence[str]) -> dict[str, str]:
        return {pid: self._passages[pid] for pid in policy_ids if pid in self._passages}
