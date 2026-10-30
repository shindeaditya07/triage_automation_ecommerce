"""The 200 test complaints for the Bulk upload page: what is committed is what the script writes,
and every row is something the real intake would accept."""

import csv
import io
import json
from collections import Counter

from scripts import make_test_complaints as gen
from services.api.intake.payloads import FormPayload
from services.api.settings import IntakeSettings


def read(path):  # type: ignore[no-untyped-def]
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))


def test_the_committed_files_are_what_the_script_writes() -> None:
    assert gen.main(["--check"]) == 0


def test_there_are_two_hundred_complaints_with_the_contact_forms_columns() -> None:
    text = gen.CASES_PATH.read_text(encoding="utf-8")
    assert next(csv.reader(io.StringIO(text))) == gen.COLUMNS
    assert len(read(gen.CASES_PATH)) == 200


def test_every_row_passes_the_real_form_rules_and_the_length_limits() -> None:
    limit = IntakeSettings(_env_file=None).form_message_max_chars  # type: ignore[call-arg]
    for row in read(gen.CASES_PATH):
        fields = {k: v for k, v in row.items() if v != ""}
        FormPayload.model_validate(fields)
        assert len(row["message"]) <= limit


def test_no_two_messages_are_the_same() -> None:
    messages = [r["message"] for r in read(gen.CASES_PATH)]
    assert len(set(messages)) == len(messages)


def test_customers_and_orders_are_ones_the_mock_store_knows() -> None:
    seed = json.loads(gen.SEED.read_text(encoding="utf-8"))
    emails = {o["customer_email"] for o in seed["orders"]}
    orders = {o["order_id"] for o in seed["orders"]}
    cases = read(gen.CASES_PATH)
    key = read(gen.KEY_PATH)
    for row, entry in zip(cases, key, strict=True):
        assert row["email"] in emails
        if entry["category"] != "order not found":
            assert row["order_id"] in orders or row["order_id"] == ""


def test_the_key_describes_every_row_and_covers_the_range() -> None:
    key = read(gen.KEY_PATH)
    assert [int(k["row"]) for k in key] == list(range(1, 201))
    assert {k["expect"] for k in key} == {gen.AUTO, gen.REVIEW, gen.ESCALATE}
    counts = Counter(k["category"] for k in key)
    assert len(counts) >= 20
    for needed in ("prompt injection", "legal threat", "safety", "order not found"):
        assert counts[needed] >= 3
