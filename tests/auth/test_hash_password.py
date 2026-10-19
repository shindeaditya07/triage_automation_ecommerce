import pytest

from scripts import hash_password
from services.api.auth.passwords import is_valid_hash


def _answers(monkeypatch: pytest.MonkeyPatch, *answers: str) -> None:
    queue = iter(answers)
    monkeypatch.setattr(hash_password.getpass, "getpass", lambda _prompt="": next(queue))


def test_prints_a_hash_that_verifies(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _answers(monkeypatch, "a long enough password", "a long enough password")
    assert hash_password.main() == 0
    out = capsys.readouterr().out.strip()
    assert is_valid_hash(out) and out.startswith("$argon2id$")
    assert "a long enough password" not in out


def test_refuses_when_the_two_entries_differ(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _answers(monkeypatch, "a long enough password", "something else entirely")
    assert hash_password.main() == 1
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("password", ["short", "x" * 1025])
def test_refuses_a_password_outside_the_length_limits(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], password: str
) -> None:
    _answers(monkeypatch, password, password)
    assert hash_password.main() == 1
    assert capsys.readouterr().out == ""
