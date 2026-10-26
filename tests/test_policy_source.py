from pathlib import Path

import pytest

from scripts import policy_lib
from services.api import policy_source
from services.api.policy_source import parse_passages, policies_version


def test_the_real_policies_file_yields_a_version_and_all_47_policy_ids() -> None:
    policy_source.expectation.cache_clear()
    found = policy_source.expectation()
    assert found.version == "1.1"
    assert len(found.policy_ids) == 47
    assert "ESC-8.2" in found.policy_ids


def test_policy_lib_shares_these_parsers() -> None:
    assert policy_lib.parse_passages is parse_passages
    assert policy_lib.policies_version is policies_version


def test_missing_version_header_is_an_error() -> None:
    with pytest.raises(ValueError, match="Policy version"):
        policies_version("# Policies\n")


def test_passages_end_at_the_next_id_a_rule_or_a_heading() -> None:
    text = "**RET-1.1 First.**\nbody one\n**RET-1.2 Second.**\nbody two\n---\nnot a passage\n"
    assert set(parse_passages(text)) == {"RET-1.1", "RET-1.2"}
    assert "not a passage" not in parse_passages(text)["RET-1.2"]


def test_policies_path_can_be_overridden(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    file = tmp_path / "p.md"
    file.write_text("Policy version **9.9**\n**RET-1.1 X.**\ntext\n", encoding="utf-8")
    monkeypatch.setenv("POLICIES_PATH", str(file))
    policy_source.expectation.cache_clear()
    try:
        found = policy_source.expectation()
        assert (found.version, found.policy_ids) == ("9.9", frozenset({"RET-1.1"}))
    finally:
        monkeypatch.delenv("POLICIES_PATH")
        policy_source.expectation.cache_clear()
