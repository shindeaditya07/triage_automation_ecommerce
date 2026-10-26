from datetime import timedelta
from uuid import uuid4

import pytest

from services.api.jobs.store import dedupe_key, input_hash
from services.api.jobs.worker import RETRY_MAX_SECONDS, describe, retry_delay
from services.api.models.enums import JobType


@pytest.mark.parametrize(
    ("attempts", "seconds"), [(1, 15), (2, 30), (3, 60), (6, 480), (7, 600), (50, 600)]
)
def test_retries_back_off_and_stop_growing(attempts: int, seconds: int) -> None:
    assert retry_delay(attempts) == timedelta(seconds=seconds)
    assert retry_delay(attempts).total_seconds() <= RETRY_MAX_SECONDS


def test_the_input_hash_ignores_key_order_and_changes_with_content() -> None:
    assert input_hash({"a": 1, "b": 2}) == input_hash({"b": 2, "a": 1})
    assert input_hash({"a": 1}) != input_hash({"a": 2})


def test_the_dedupe_key_names_type_ticket_and_input() -> None:
    ticket = uuid4()
    key = dedupe_key(JobType.PROCESS_TICKET, ticket, "abc")
    assert key == f"process_ticket:{ticket}:abc"
    assert dedupe_key(JobType.RETENTION_SWEEP, None, "abc") == "retention_sweep:-:abc"


def test_an_error_is_described_by_its_type_and_message() -> None:
    assert describe(ValueError("boom")) == "ValueError: boom"
    assert describe(TimeoutError()) == "TimeoutError"
