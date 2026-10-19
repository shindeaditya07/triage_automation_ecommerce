"""Argon2id password hashing (docs/AGENTS.md section 7: argon2, not bcrypt).

Hashing is CPU and memory heavy on purpose, so verification runs in a worker thread and never
blocks the event loop (docs/adr/0001-concurrency.md).
"""

import asyncio
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MAX_PASSWORD_LENGTH = 1024  # bounds the work an unauthenticated caller can ask for

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


@lru_cache(maxsize=1)
def dummy_hash() -> str:
    """A valid hash nobody knows the password for. Checked when the email is wrong, so a wrong
    email and a wrong password take the same time."""
    return _hasher.hash("this is not anybody's password")


def is_valid_hash(stored: str) -> bool:
    try:
        _hasher.check_needs_rehash(stored)
    except (InvalidHashError, VerificationError):
        return False
    return True


def _verify(stored: str, password: str) -> bool:
    try:
        return _hasher.verify(stored, password)
    except VerifyMismatchError:
        return False
    except (InvalidHashError, VerificationError):
        return False  # fail closed: a hash we cannot read never matches


async def verify_password(stored: str, password: str) -> bool:
    return await asyncio.to_thread(_verify, stored, password)
