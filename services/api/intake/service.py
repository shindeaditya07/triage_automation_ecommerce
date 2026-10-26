"""Intake: clean a message, then store it. Both endpoints come through here."""

from services.api.clock import Clock
from services.api.intake.cleaning import clean_request
from services.api.intake.store import IngestResult, TicketStore
from services.api.models.ticket import TicketCreateRequest


class IntakeService:
    def __init__(self, store: TicketStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def ingest(self, request: TicketCreateRequest) -> IngestResult:
        """Sanitise and mask the message, then store it. Raises `EmptyMessageError` if nothing
        is left. The stored text is the cleaned text; the original is not kept."""
        now = self._clock()
        cleaned, report = clean_request(request, now)
        return await self._store.add(cleaned, report, now)
