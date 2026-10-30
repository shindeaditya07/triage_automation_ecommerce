"""Customer erasure: validate the address, erase through the store, report the count."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel

from services.api.auth.service import Owner
from services.api.clock import Clock


class Eraser(Protocol):
    async def erase_customer(self, email: str, actor: str, now: datetime) -> int: ...


class ErasureResult(BaseModel):
    erased: int


class InvalidEmail(Exception):
    pass


MAX_EMAIL = 320


def normalise(raw: str) -> str:
    """Tickets store the address lowercase and trimmed; match it the same way."""
    email = raw.strip().lower()
    local, _, domain = email.partition("@")
    if not local or "." not in domain or len(email) > MAX_EMAIL or any(c.isspace() for c in email):
        raise InvalidEmail("not an email address")
    return email


@dataclass
class ErasureService:
    store: Eraser
    clock: Clock

    async def erase(self, raw_email: str, owner: Owner) -> ErasureResult:
        email = normalise(raw_email)
        count = await self.store.erase_customer(email, owner.email, self.clock())
        return ErasureResult(erased=count)
