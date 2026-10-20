import pytest

from services.api.models.enums import Intent
from services.api.policy_source import expectation
from services.api.retrieval import catalog


def test_every_intent_has_an_entry() -> None:
    assert set(catalog.BY_INTENT) == set(Intent)


def test_every_named_passage_exists_in_the_loaded_policies() -> None:
    named = {*catalog.ALWAYS, *catalog.SUGGESTION_ONLY}
    for ids in catalog.BY_INTENT.values():
        named.update(ids)
    assert catalog.unknown_policy_ids(named) == []
    assert named <= expectation().policy_ids


def test_a_damage_report_always_carries_the_window_and_the_photo_rule() -> None:
    ids = catalog.deterministic_ids(Intent.DAMAGED_ITEM)
    assert {"DMG-3.1", "DMG-3.4"} <= set(ids)


@pytest.mark.parametrize("intent", list(Intent))
def test_the_no_promises_rule_is_always_first(intent: Intent) -> None:
    assert catalog.deterministic_ids(intent)[0] == "TON-9.2"


def test_extra_ids_are_added_without_repeats() -> None:
    ids = catalog.deterministic_ids(Intent.RETURN, ["ESC-8.3", "RET-1.1", "TON-9.2"])
    assert ids.count("RET-1.1") == 1 and ids.count("TON-9.2") == 1
    assert ids[-1] == "ESC-8.3"


def test_unknown_ids_are_reported_sorted_and_once() -> None:
    assert catalog.unknown_policy_ids(["RET-1.1", "ZZZ-9.9", "AAA-1.1", "ZZZ-9.9"]) == [
        "AAA-1.1",
        "ZZZ-9.9",
    ]
