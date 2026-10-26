"""Types shared by several models."""

from datetime import timedelta
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict


def _require_utc(value: AwareDatetime) -> AwareDatetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamps must be in UTC (offset +00:00 / Z)")
    return value


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_require_utc)]
"""Timezone-aware UTC timestamp. Naive datetimes and non-UTC offsets are rejected."""


class StrictModel(BaseModel):
    """Base for every model: unknown fields are errors, so a typo cannot slip through."""

    model_config = ConfigDict(extra="forbid")
