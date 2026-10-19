import pytest

from services.api.auth.store import AuthStore, InMemoryAuthStore
from tests.auth.store_contract import StoreContract


class TestInMemoryStore(StoreContract):
    @pytest.fixture
    def store(self) -> AuthStore:
        return InMemoryAuthStore()
