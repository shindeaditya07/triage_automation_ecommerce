import json
import shutil
from pathlib import Path

import pytest
import yaml

from scripts import check_policy_constants, check_policy_sha
from scripts.policy_lib import (
    CONSTANTS_PATH,
    LOCK_PATH,
    POLICIES_PATH,
    expected_token,
    parse_passages,
    policies_sha256,
)

DOC_VALUES = {
    "return_window_days": 7,
    "damage_window_hours": 48,
    "free_shipping_threshold_paise": 99900,
    "shipping_fee_paise": 6000,
    "high_value_threshold_paise": 1000000,
    "cod_limit_paise": 500000,
    "refund_business_days_min": 5,
    "refund_business_days_max": 7,
    "warranty_months": 6,
    "dispatch_window_days_min": 1,
    "dispatch_window_days_max": 2,
}


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    shutil.copy(POLICIES_PATH, tmp_path / "policies.md")
    shutil.copy(CONSTANTS_PATH, tmp_path / "policy_constants.yaml")
    shutil.copy(LOCK_PATH, tmp_path / ".policy_lock")
    return tmp_path


def run_constants(workdir: Path) -> list[str]:
    return check_policy_constants.check(workdir / "policy_constants.yaml", workdir / "policies.md")


def run_sha(workdir: Path) -> list[str]:
    return check_policy_sha.check(workdir / "policies.md", workdir / ".policy_lock")


def flat_constants() -> dict[str, int]:
    data = yaml.safe_load(CONSTANTS_PATH.read_text(encoding="utf-8"))
    return {
        name: spec["value"]
        for constants in data["constants"].values()
        for name, spec in constants.items()
    }


def test_repo_files_are_consistent() -> None:
    assert check_policy_constants.check() == []
    assert check_policy_sha.check() == []


def test_constants_cover_the_values_listed_in_the_policy_dev_notes() -> None:
    values = flat_constants()
    for name, value in DOC_VALUES.items():
        assert values[name] == value, name


def test_passage_parser_finds_ids_and_stops_at_section_breaks() -> None:
    passages = parse_passages(POLICIES_PATH.read_text(encoding="utf-8"))
    assert "RET-1.1" in passages and "FAQ-10.8" in passages
    assert "RET-1.2" not in passages["RET-1.1"]
    assert "## 2. Refunds" not in passages["RET-1.5"]


def test_paise_values_are_compared_as_rupees() -> None:
    assert expected_token(99900, "paise") == "₹999"
    assert expected_token(1000000, "paise") == "₹10000"
    with pytest.raises(ValueError):
        expected_token(150, "paise")


def test_wrong_constant_value_fails(workdir: Path) -> None:
    path = workdir / "policy_constants.yaml"
    path.write_text(
        path.read_text().replace(
            'value: 7\n      unit: days\n      text: "7 calendar',
            'value: 8\n      unit: days\n      text: "7 calendar',
        )
    )
    assert any("return_window_days" in e for e in run_constants(workdir))


def test_old_wrong_free_shipping_value_fails(workdir: Path) -> None:
    path = workdir / "policy_constants.yaml"
    path.write_text(path.read_text().replace("value: 99900", "value: 50000"))
    assert any("free_shipping_threshold_paise" in e for e in run_constants(workdir))


def test_unknown_policy_id_fails(workdir: Path) -> None:
    path = workdir / "policy_constants.yaml"
    path.write_text(path.read_text().replace("  RET-1.1:", "  RET-2.1:"))
    assert any("RET-2.1: no such passage" in e for e in run_constants(workdir))


def test_changed_passage_text_fails(workdir: Path) -> None:
    path = workdir / "policies.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "7 calendar days of the delivery date", "10 calendar days of the delivery date"
        ),
        encoding="utf-8",
    )
    assert any("return_window_days" in e for e in run_constants(workdir))


def test_version_mismatch_fails(workdir: Path) -> None:
    path = workdir / "policy_constants.yaml"
    path.write_text(path.read_text().replace('policy_version: "1.1"', 'policy_version: "1.0"'))
    assert any("policy_version" in e for e in run_constants(workdir))


def test_policy_edit_without_version_bump_fails_the_lock(workdir: Path) -> None:
    path = workdir / "policies.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nextra line\n", encoding="utf-8")
    errors = run_sha(workdir)
    assert errors and "still 1.1" in errors[0]


def test_version_bump_without_lock_update_fails(workdir: Path) -> None:
    path = workdir / "policies.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "Policy version **1.1**", "Policy version **1.2**"
        ),
        encoding="utf-8",
    )
    errors = run_sha(workdir)
    assert errors and "--update" in errors[0]


def test_lock_update_makes_the_check_pass(workdir: Path) -> None:
    path = workdir / "policies.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "Policy version **1.1**", "Policy version **1.2**"
        ),
        encoding="utf-8",
    )
    check_policy_sha.update(workdir / "policies.md", workdir / ".policy_lock")
    assert run_sha(workdir) == []
    assert json.loads((workdir / ".policy_lock").read_text())["version"] == "1.2"


def test_crlf_checkout_hashes_the_same(workdir: Path) -> None:
    path = workdir / "policies.md"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert policies_sha256(path) == policies_sha256(POLICIES_PATH)
    assert run_sha(workdir) == []
