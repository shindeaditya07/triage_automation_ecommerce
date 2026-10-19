import asyncio

import pytest

from services.api.auth import passwords


def test_hash_is_argon2id_and_verifies(owner_hash: str) -> None:
    assert owner_hash.startswith("$argon2id$")
    assert asyncio.run(passwords.verify_password(owner_hash, "correct horse battery staple"))


def test_wrong_password_does_not_verify(owner_hash: str) -> None:
    assert not asyncio.run(passwords.verify_password(owner_hash, "wrong"))


@pytest.mark.parametrize("stored", ["", "plaintext", "$argon2id$garbage", "$2b$12$abcdefghij"])
def test_an_unreadable_hash_never_verifies_and_is_reported_invalid(stored: str) -> None:
    assert not passwords.is_valid_hash(stored)
    assert not asyncio.run(passwords.verify_password(stored, "anything"))


def test_valid_hash_is_recognised(owner_hash: str) -> None:
    assert passwords.is_valid_hash(owner_hash)


def test_dummy_hash_is_valid_and_not_the_owners(owner_hash: str) -> None:
    assert passwords.is_valid_hash(passwords.dummy_hash())
    assert passwords.dummy_hash() != owner_hash
    assert passwords.dummy_hash() == passwords.dummy_hash()  # computed once
