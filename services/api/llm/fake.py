"""FakeLLMClient: returns recorded outputs so CI never makes a live model call.

Only `classify` exists so far. `draft`, `judge` and `embed`, and the shared `LLMClient` interface,
arrive with feature/p2-llm-client, which makes this class implement that interface.
"""

import json
from pathlib import Path

from services.api.models.enums import Flag, Intent, Sentiment, Urgency
from services.api.models.ticket import Classification


class RecordedOutputMissing(LookupError):
    """No recorded output for a request. Fails closed: a missing recording is never a guess."""


class FakeLLMClient:
    model = "fake-recorded"
    temperature = 0.0

    def __init__(self, classifications: dict[str, Classification]) -> None:
        self._classifications = dict(classifications)

    @classmethod
    def from_jsonl(cls, path: Path) -> "FakeLLMClient":
        """Load `{"ticket_id": ..., "classification": {...}}` rows (extra keys are ignored)."""
        recorded: dict[str, Classification] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            body = row["classification"]
            recorded[row["ticket_id"]] = Classification(
                intent=Intent(body["intent"]),
                urgency=Urgency(body["urgency"]),
                confidence=body["confidence"],
                sentiment=Sentiment(body["sentiment"]),
                flags=[Flag(flag) for flag in body.get("flags", [])],
            )
        return cls(recorded)

    def classify(self, ticket_id: str) -> Classification:
        try:
            return self._classifications[ticket_id]
        except KeyError:
            raise RecordedOutputMissing(f"no recorded classification for {ticket_id!r}") from None
