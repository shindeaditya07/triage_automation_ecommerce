"""Behaviour every AuthStore must have. tests/auth runs it on the in-memory store; tests/db runs
the same checks on Postgres, so the two cannot drift apart."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from services.api.auth.lockout import LockoutPolicy
from services.api.auth.store import AuthStore, RotateOutcome
from tests.auth.conftest import START

POLICY = LockoutPolicy(max_failures=3, base_seconds=60, max_seconds=3600)
DAY = timedelta(days=1)


@pytest.mark.anyio
class StoreContract:
    """Subclasses provide `store` (a fresh AuthStore) as a fixture."""

    async def test_attempts_are_allowed_until_the_limit_then_the_account_locks(
        self, store: AuthStore
    ) -> None:
        assert [await store.begin_login_attempt(POLICY, START) for _ in range(3)] == [0, 0, 0]
        assert await store.begin_login_attempt(POLICY, START + timedelta(seconds=10)) == 50

    async def test_the_lock_lifts_when_its_time_is_up(self, store: AuthStore) -> None:
        for _ in range(3):
            await store.begin_login_attempt(POLICY, START)
        assert await store.begin_login_attempt(POLICY, START + timedelta(seconds=61)) == 0

    async def test_success_clears_the_count_and_the_lock(self, store: AuthStore) -> None:
        for _ in range(3):
            await store.begin_login_attempt(POLICY, START)
        await store.record_login_success()
        assert await store.begin_login_attempt(POLICY, START) == 0

    async def test_parallel_attempts_cannot_exceed_the_limit(self, store: AuthStore) -> None:
        waits = await asyncio.gather(*(store.begin_login_attempt(POLICY, START) for _ in range(20)))
        assert waits.count(0) == POLICY.max_failures

    async def test_a_refresh_token_rotates_once(self, store: AuthStore) -> None:
        sid, first, second = uuid4(), uuid4(), uuid4()
        await store.issue_refresh(first, sid, START, START + 7 * DAY)
        now = START + timedelta(hours=1)
        assert (
            await store.rotate_refresh(first, sid, second, now, now + 7 * DAY)
            is RotateOutcome.ROTATED
        )

    async def test_reusing_a_rotated_token_revokes_the_whole_session(
        self, store: AuthStore
    ) -> None:
        sid, first, second, third = uuid4(), uuid4(), uuid4(), uuid4()
        await store.issue_refresh(first, sid, START, START + 7 * DAY)
        now = START + timedelta(hours=1)
        await store.rotate_refresh(first, sid, second, now, now + 7 * DAY)
        reuse = await store.rotate_refresh(first, sid, third, now, now + 7 * DAY)
        assert reuse is RotateOutcome.REUSED
        after = await store.rotate_refresh(second, sid, third, now, now + 7 * DAY)
        assert after is RotateOutcome.REUSED  # the live token died with the session

    async def test_an_unknown_token_or_the_wrong_session_is_unknown(self, store: AuthStore) -> None:
        sid, jti = uuid4(), uuid4()
        await store.issue_refresh(jti, sid, START, START + 7 * DAY)
        now = START + timedelta(hours=1)
        unknown = await store.rotate_refresh(uuid4(), sid, uuid4(), now, now + DAY)
        assert unknown is RotateOutcome.UNKNOWN
        wrong_session = await store.rotate_refresh(jti, uuid4(), uuid4(), now, now + DAY)
        assert wrong_session is RotateOutcome.UNKNOWN

    async def test_an_expired_token_does_not_rotate(self, store: AuthStore) -> None:
        sid, jti = uuid4(), uuid4()
        await store.issue_refresh(jti, sid, START, START + DAY)
        now = START + 2 * DAY
        assert (
            await store.rotate_refresh(jti, sid, uuid4(), now, now + DAY) is RotateOutcome.EXPIRED
        )

    async def test_the_exact_expiry_instant_is_expired(self, store: AuthStore) -> None:
        sid, jti = uuid4(), uuid4()
        await store.issue_refresh(jti, sid, START, START + DAY)
        outcome = await store.rotate_refresh(jti, sid, uuid4(), START + DAY, START + 2 * DAY)
        assert outcome is RotateOutcome.EXPIRED

    async def test_revoking_a_session_stops_its_tokens_and_spares_others(
        self, store: AuthStore
    ) -> None:
        mine, other = uuid4(), uuid4()
        mine_jti, other_jti = uuid4(), uuid4()
        await store.issue_refresh(mine_jti, mine, START, START + 7 * DAY)
        await store.issue_refresh(other_jti, other, START, START + 7 * DAY)
        await store.revoke_session(mine, START)
        now = START + timedelta(hours=1)
        assert (
            await store.rotate_refresh(mine_jti, mine, uuid4(), now, now + DAY)
            is RotateOutcome.REUSED
        )
        assert (
            await store.rotate_refresh(other_jti, other, uuid4(), now, now + DAY)
            is RotateOutcome.ROTATED
        )

    async def test_revoking_an_unknown_session_is_harmless(self, store: AuthStore) -> None:
        await store.revoke_session(uuid4(), START)

    async def test_long_expired_tokens_are_purged_when_a_new_one_is_issued(
        self, store: AuthStore
    ) -> None:
        sid, old = uuid4(), uuid4()
        await store.issue_refresh(old, sid, START, START + DAY)
        later = START + 10 * DAY
        await store.issue_refresh(uuid4(), uuid4(), later, later + DAY)
        outcome = await store.rotate_refresh(old, sid, uuid4(), later, later + DAY)
        assert outcome is RotateOutcome.UNKNOWN  # gone, not merely expired
