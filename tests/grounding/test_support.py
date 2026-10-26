from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.api.grounding import config as grounding_config
from services.api.grounding import extract
from services.api.grounding.extract import DayOfYear, Duration
from services.api.grounding.models import RuleFailure, ValidationResult
from services.api.grounding.order_index import OrderIndex, covered, normalise_ref
from tests.grounding.conftest import ORDER

# --- extractors ---


def test_amounts_become_paise() -> None:
    found = extract.amounts("It was ₹1,299.50, Rs 40, Rs. 5 and INR 7.")
    assert [f.value for f in found] == [129950, 4000, 500, 700]
    assert extract.amounts("no money here") == []


def test_durations_units_ranges_and_words() -> None:
    values = {f.value for f in extract.durations("7 calendar days, 3-5 business days, two weeks")}
    assert values == {Duration(7, 7, "day"), Duration(3, 5, "day"), Duration(2, 2, "week")}
    assert extract.durations("3 to 5 days")[0].value == Duration(3, 5, "day")
    assert extract.durations("5 to 3 hrs")[0].value == Duration(3, 5, "hour")
    assert extract.durations("1 month")[0].value.unit == "month"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("on 12 October 2026", DayOfYear(12, 10, 2026)),
        ("on 12th Oct", DayOfYear(12, 10, None)),
        ("on October 12, 2026", DayOfYear(12, 10, 2026)),
        ("on Oct 3", DayOfYear(3, 10, None)),
        ("on 2026-10-12", DayOfYear(12, 10, 2026)),
        ("on 12/10/2026", DayOfYear(12, 10, 2026)),
    ],
)
def test_date_formats(text: str, expected: DayOfYear) -> None:
    assert [f.value for f in extract.dates(text)] == [expected]


def test_dates_do_not_double_count_or_accept_nonsense() -> None:
    assert len(extract.dates("2026-10-12")) == 1
    assert extract.dates("on 45/13/2026") == []
    assert extract.dates("no dates") == []
    assert [f.value for f in extract.dates("3 October 12")] == [DayOfYear(3, 10, None)]


def test_day_of_year_match() -> None:
    assert DayOfYear(12, 10, None).matches(date(2026, 10, 12))
    assert DayOfYear(12, 10, 2026).matches(date(2026, 10, 12))
    assert not DayOfYear(12, 10, 2025).matches(date(2026, 10, 12))
    assert not DayOfYear(13, 10, None).matches(date(2026, 10, 12))


def test_small_helpers() -> None:
    assert extract.normalise("It’s “fine”\n\n ok") == 'It\'s "fine" ok'
    assert extract.emails("Mail Bob@Example.com now") == ["bob@example.com"]
    assert extract.policy_ids("see RET-1.1 and REF-2.3") == ["RET-1.1", "REF-2.3"]
    assert extract.sentences("One. Two!\nThree") == ["One.", "Two!", "Three"]
    cfg = grounding_config.default_config()
    assert extract.matches_of(
        cfg.compiled("order_id_patterns"), "rd-10001 and RD10001 RD-10001"
    ) == [
        "RD-10001",
        "RD10001",
    ]


# --- order index ---


@pytest.mark.parametrize(
    ("ref", "path"),
    [
        ("order_id", "order_id"),
        ("orders.delivered_at", "delivered_at"),
        ("order.items[0].unit_price_paise", "items.unit_price_paise"),
        ("items.0.quantity", "items.quantity"),
        ("  courier_events[2].status ", "courier_events.status"),
    ],
)
def test_normalise_ref(ref: str, path: str) -> None:
    assert normalise_ref(ref) == path


def test_order_index_contents() -> None:
    index = OrderIndex.build(ORDER)
    assert index.has("orders.items[0].product_name")
    assert not index.has("nope")
    assert index.amount_paths(64900) == {"order_value_paise", "items.unit_price_paise"}
    assert index.amount_paths(1) == set()
    assert index.date_paths(DayOfYear(12, 10, 2026)) == {"delivered_at"}
    assert index.date_paths(DayOfYear(7, 10, None)) == {"placed_at"}
    assert index.token_paths("TRK100010000") == {"courier_tracking_number"}
    assert "steel water bottle" in index.text and "local depot" in index.text


def test_order_index_converts_dates_to_the_store_timezone() -> None:
    late = OrderIndex.build({"delivered_at": "2026-10-12T20:00:00Z"})  # 01:30 next day in IST
    assert late.date_paths(DayOfYear(13, 10, 2026)) == {"delivered_at"}
    assert late.date_paths(DayOfYear(12, 10, 2026)) == set()


def test_order_index_ignores_booleans_nulls_and_non_amount_integers() -> None:
    index = OrderIndex.build({"flag": True, "nothing": None, "quantity": 3, "fee_paise": 50})
    assert index.amount_values == [("fee_paise", 50)]
    assert {"flag", "nothing", "quantity"} <= index.paths


def test_covered_by_exact_or_parent_path() -> None:
    assert covered({"items"}, {"items.unit_price_paise"})
    assert covered({"order_value_paise"}, {"order_value_paise"})
    assert not covered({"item"}, {"items.unit_price_paise"})
    assert not covered(set(), {"order_id"})


# --- config ---

MINIMAL = """
max_reply_chars: 100
commitment_patterns: ["promise"]
apology_patterns: ["sorry"]
obedience_patterns: ["obey"]
causal_patterns: []
process_terms: []
product_claim_phrases: []
couriers: []
allowed_names: []
order_id_patterns: ["RD-\\\\d+"]
tracking_patterns: []
fallback_template: "Hello.\\n\\n{sign_off}"
"""


def test_the_committed_config_loads() -> None:
    cfg = grounding_config.default_config()
    assert cfg.max_reply_chars == 1500 and cfg.commitment_patterns
    assert cfg.compiled("apology_patterns")[0].search("I am SORRY")


def test_config_from_a_path_and_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = tmp_path / "g.yaml"
    file.write_text(MINIMAL, encoding="utf-8")
    assert grounding_config.load_config(file).max_reply_chars == 100
    monkeypatch.setenv("GROUNDING_CONFIG_PATH", str(file))
    assert grounding_config.load_config().max_reply_chars == 100


def test_the_fallback_template_is_filled_and_must_end_with_the_sign_off(tmp_path: Path) -> None:
    cfg = grounding_config.default_config()
    text = cfg.fallback_text("Test Store")
    assert text.endswith("Test Store") and "{sign_off}" not in text
    file = tmp_path / "g.yaml"
    for bad in ('"Hello."', '"{sign_off} then more"', '"{sign_off} and {sign_off}"'):
        file.write_text(MINIMAL.replace('"Hello.\\n\\n{sign_off}"', bad), encoding="utf-8")
        with pytest.raises(ValidationError):
            grounding_config.load_config(file)


def test_a_bad_regex_is_rejected_at_load(tmp_path: Path) -> None:
    file = tmp_path / "g.yaml"
    file.write_text(MINIMAL.replace('["promise"]', '["(unclosed"]'), encoding="utf-8")
    with pytest.raises(ValidationError):
        grounding_config.load_config(file)


def test_an_unknown_or_missing_key_is_rejected(tmp_path: Path) -> None:
    file = tmp_path / "g.yaml"
    file.write_text(MINIMAL + "surprise: 1\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        grounding_config.load_config(file)
    file.write_text(MINIMAL.replace("max_reply_chars: 100\n", ""), encoding="utf-8")
    with pytest.raises(ValidationError):
        grounding_config.load_config(file)


# --- results ---


def test_validation_result_helpers() -> None:
    assert ValidationResult().passed and ValidationResult().rule_ids == set()
    failure = RuleFailure(rule_id="G-3", span="we will refund", detail="promises an outcome")
    result = ValidationResult(failures=[failure])
    assert not result.passed and result.rule_ids == {"G-3"}
    assert result.reasons() == ["G-3: promises an outcome ('we will refund')"]
    with pytest.raises(ValidationError):
        RuleFailure(rule_id="G-11", span="x", detail="y")
