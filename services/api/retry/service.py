"""Retry a failed ticket on the owner's request."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel

from services.api.auth.service import Owner
from services.api.clock import Clock
from services.api.models.enums import TicketStatus


class Retrier(Protocol):
    async def retry(self, ticket_id: UUID, actor: str, now: datetime) -> TicketStatus: ...


class RetryResult(BaseModel):
    ticket_id: UUID
    status: TicketStatus


@dataclass
class RetryService:
    store: Retrier
    clock: Clock

    async def retry(self, ticket_id: UUID, owner: Owner) -> RetryResult:
        status = await self.store.retry(ticket_id, owner.email, self.clock())
        return RetryResult(ticket_id=ticket_id, status=status)
