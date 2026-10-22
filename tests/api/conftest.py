"""Fixtures for the route tests: the owner-auth settings, store and clock."""

from tests.auth.conftest import (  # noqa: F401 - pytest fixtures
    clock,
    make_settings,
    owner_hash,
    store,
)
