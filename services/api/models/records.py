"""Rows outside the public ticket/draft schemas: audit log, jobs, snapshots, policy index."""

from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from services.api.models.common import StrictModel, UtcDatetime
from services.api.models.enums import JobStatus, JobType

EMBEDDING_DIMENSIONS = 1536
"""Width of policy_embeddings.embedding. Changing the embedding model's size needs a migration."""


class AuditEvent(StrictModel):
    """Append-only: the database rejects updates and deletes on audit_log."""

    event_id: int | None = None
    ticket_id: UUID | None = None
    event_type: str = Field(min_length=1)
    actor: str = Field(min_length=1, description="'system', 'owner' or a component name")
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: UtcDatetime | None = None


class Job(StrictModel):
    job_id: UUID
    ticket_id: UUID | None = None
    job_type: JobType
    status: JobStatus = JobStatus.QUEUED
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    run_at: UtcDatetime
    locked_at: UtcDatetime | None = None
    locked_by: str | None = None
    last_error: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def _lock_matches_status(self) -> "Job":
        if (self.status == JobStatus.RUNNING) != (self.locked_at is not None):
            raise ValueError("locked_at is set exactly while the job is running")
        return self


class OrderSnapshot(StrictModel):
    """The order exactly as the store returned it when a draft was made."""

    snapshot_id: UUID
    ticket_id: UUID
    order_id: str = Field(min_length=1)
    taken_at: UtcDatetime
    data: dict[str, Any]


class PolicyEmbedding(StrictModel):
    policy_id: str = Field(pattern=r"^[A-Z]{3}-\d+\.\d+$")
    policy_version: str = Field(min_length=1)
    passage_text: str = Field(min_length=1)
    embed_model: str = Field(min_length=1)
    embedding: list[float] = Field(min_length=EMBEDDING_DIMENSIONS, max_length=EMBEDDING_DIMENSIONS)
    created_at: UtcDatetime | None = None
