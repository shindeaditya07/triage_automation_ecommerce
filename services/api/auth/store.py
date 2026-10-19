"""Where owner-auth state lives: the login lockout and the refresh tokens.

`PostgresAuthStore` is the real one. `InMemoryAuthStore` follows the same rules for fast tests
and for local runs without a database; tests/db runs one contract suite against both so they
cannot drift apart.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from services.api.auth.lockout import LockoutPolicy, LockoutState
from services.api.db.tables import auth_lockout, auth_refresh_tokens

PURGE_AFTER = timedelta(days=1)  # keep expired tokens a day, then delete them


class RotateOutcome(StrEnum):
    ROTATED = "rotated"
    UNKNOWN = "unknown"  # no such token for this session
    EXPIRED = "expired"
    REUSED = "reused"  # already rotated or revoked: the whole session is revoked


class AuthStore(Protocol):
    async def begin_login_attempt(self, policy: LockoutPolicy, now: datetime) -> int:
        """Count an attempt. Returns 0 to go ahead, or the seconds to wait while locked."""
        ...

    async def record_login_success(self) -> None: ...

    async def issue_refresh(
        self, jti: UUID, sid: UUID, issued_at: datetime, expires_at: datetime
    ) -> None: ...

    async def rotate_refresh(
        self, old_jti: UUID, sid: UUID, new_jti: UUID, now: datetime, new_expires_at: datetime
    ) -> RotateOutcome: ...

    async def revoke_session(self, sid: UUID, now: datetime) -> None: ...


@dataclass
class _Refresh:
    sid: UUID
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    replaced_by: UUID | None = None


class InMemoryAuthStore:
    def __init__(self) -> None:
        self._lockout = LockoutState()
        self._refresh: dict[UUID, _Refresh] = {}

    async def begin_login_attempt(self, policy: LockoutPolicy, now: datetime) -> int:
        self._lockout, wait = policy.begin_attempt(self._lockout, now)
        return wait

    async def record_login_success(self) -> None:
        self._lockout = LockoutState()

    async def issue_refresh(
        self, jti: UUID, sid: UUID, issued_at: datetime, expires_at: datetime
    ) -> None:
        cutoff = issued_at - PURGE_AFTER
        self._refresh = {k: v for k, v in self._refresh.items() if v.expires_at >= cutoff}
        self._refresh[jti] = _Refresh(sid, issued_at, expires_at)

    async def rotate_refresh(
        self, old_jti: UUID, sid: UUID, new_jti: UUID, now: datetime, new_expires_at: datetime
    ) -> RotateOutcome:
        old = self._refresh.get(old_jti)
        if old is None or old.sid != sid:
            return RotateOutcome.UNKNOWN
        if old.revoked_at is not None:
            await self.revoke_session(sid, now)
            return RotateOutcome.REUSED
        if old.expires_at <= now:
            return RotateOutcome.EXPIRED
        old.revoked_at = now
        old.replaced_by = new_jti
        self._refresh[new_jti] = _Refresh(sid, now, new_expires_at)
        return RotateOutcome.ROTATED

    async def revoke_session(self, sid: UUID, now: datetime) -> None:
        for token in self._refresh.values():
            if token.sid == sid and token.revoked_at is None:
                token.revoked_at = now

    # --- test helpers -------------------------------------------------------------------------

    def lockout_state(self) -> LockoutState:
        return replace(self._lockout)

    def is_revoked(self, jti: UUID) -> bool:
        return self._refresh[jti].revoked_at is not None


class PostgresAuthStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def begin_login_attempt(self, policy: LockoutPolicy, now: datetime) -> int:
        # The row lock is held only for these two statements, never across the password check.
        async with self._engine.begin() as conn:
            row = (
                await conn.execute(
                    select(auth_lockout.c.consecutive_failures, auth_lockout.c.locked_until)
                    .where(auth_lockout.c.id == 1)
                    .with_for_update()
                )
            ).one()
            state, wait = policy.begin_attempt(LockoutState(row[0], row[1]), now)
            if wait == 0:
                await conn.execute(
                    update(auth_lockout)
                    .where(auth_lockout.c.id == 1)
                    .values(
                        consecutive_failures=state.consecutive_failures,
                        locked_until=state.locked_until,
                        updated_at=now,
                    )
                )
            return wait

    async def record_login_success(self) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                update(auth_lockout)
                .where(auth_lockout.c.id == 1)
                .values(consecutive_failures=0, locked_until=None)
            )

    async def issue_refresh(
        self, jti: UUID, sid: UUID, issued_at: datetime, expires_at: datetime
    ) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                delete(auth_refresh_tokens).where(
                    auth_refresh_tokens.c.expires_at < issued_at - PURGE_AFTER
                )
            )
            await conn.execute(
                insert(auth_refresh_tokens).values(
                    jti=jti, sid=sid, issued_at=issued_at, expires_at=expires_at
                )
            )

    async def rotate_refresh(
        self, old_jti: UUID, sid: UUID, new_jti: UUID, now: datetime, new_expires_at: datetime
    ) -> RotateOutcome:
        async with self._engine.begin() as conn:
            old = (
                await conn.execute(
                    select(
                        auth_refresh_tokens.c.sid,
                        auth_refresh_tokens.c.expires_at,
                        auth_refresh_tokens.c.revoked_at,
                    )
                    .where(auth_refresh_tokens.c.jti == old_jti)
                    .with_for_update()
                )
            ).one_or_none()
            if old is None or old.sid != sid:
                return RotateOutcome.UNKNOWN
            if old.revoked_at is not None:
                await self._revoke(conn, sid, now)
                return RotateOutcome.REUSED
            if old.expires_at <= now:
                return RotateOutcome.EXPIRED
            await conn.execute(
                update(auth_refresh_tokens)
                .where(auth_refresh_tokens.c.jti == old_jti)
                .values(revoked_at=now, replaced_by=new_jti)
            )
            await conn.execute(
                insert(auth_refresh_tokens).values(
                    jti=new_jti, sid=sid, issued_at=now, expires_at=new_expires_at
                )
            )
            return RotateOutcome.ROTATED

    async def revoke_session(self, sid: UUID, now: datetime) -> None:
        async with self._engine.begin() as conn:
            await self._revoke(conn, sid, now)

    @staticmethod
    async def _revoke(conn: AsyncConnection, sid: UUID, now: datetime) -> None:
        await conn.execute(
            update(auth_refresh_tokens)
            .where(auth_refresh_tokens.c.sid == sid, auth_refresh_tokens.c.revoked_at.is_(None))
            .values(revoked_at=now)
        )
