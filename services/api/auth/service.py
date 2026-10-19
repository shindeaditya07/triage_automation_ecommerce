"""Owner login, session refresh and logout. No HTTP here: routes.py makes the responses."""

import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from services.api.auth.lockout import LockoutPolicy
from services.api.auth.passwords import dummy_hash, is_valid_hash, verify_password
from services.api.auth.ratelimit import SlidingWindowLimiter
from services.api.auth.store import AuthStore, RotateOutcome
from services.api.auth.tokens import TokenError, TokenIssuer, TokenType
from services.api.clock import Clock
from services.api.settings import AuthSettings


class AuthError(Exception):
    def __init__(self, status_code: int, detail: str, retry_after: int | None = None) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.retry_after = retry_after


NOT_AUTHENTICATED = AuthError(401, "not authenticated")


@dataclass(frozen=True)
class IssuedSession:
    sid: UUID
    access_token: str
    refresh_token: str
    csrf_token: str


@dataclass(frozen=True)
class Owner:
    sid: UUID
    email: str


class AuthService:
    def __init__(self, settings: AuthSettings, store: AuthStore | None, clock: Clock) -> None:
        self.settings = settings
        self.store = store
        self._clock = clock
        self.issuer = TokenIssuer(
            settings.secret_key.get_secret_value(),
            clock,
            access_ttl=timedelta(minutes=settings.access_token_minutes),
            refresh_ttl=timedelta(days=settings.refresh_token_days),
        )
        self._policy = LockoutPolicy(
            settings.login_max_failures,
            settings.login_lockout_base_seconds,
            settings.login_lockout_max_seconds,
        )
        self._limiter = SlidingWindowLimiter(
            settings.login_ip_max_attempts, settings.login_ip_window_seconds, clock
        )
        self._owner_email = settings.owner_email
        self._owner_hash = (
            settings.owner_password_hash.get_secret_value()
            if settings.owner_password_hash
            else None
        )

    def _ready(self) -> tuple[AuthStore, str, str]:
        """The store, owner email and owner hash, or a 503 if login cannot work: the owner is not
        configured, the hash is unreadable, or there is no database to keep state in."""
        if (
            self.store is None
            or self._owner_email is None
            or self._owner_hash is None
            or not is_valid_hash(self._owner_hash)
        ):
            raise AuthError(503, "authentication is not available")
        return self.store, self._owner_email, self._owner_hash

    async def login(self, email: str, password: str, ip: str) -> IssuedSession:
        store, owner_email, owner_hash = self._ready()
        wait = self._limiter.hit(ip)
        if wait:
            raise AuthError(429, "too many login attempts", wait)
        wait = await store.begin_login_attempt(self._policy, self._clock())
        if wait:
            raise AuthError(429, "too many login attempts", wait)
        email_ok = secrets.compare_digest(email.strip().lower().encode(), owner_email.encode())
        # Always run a verification so a wrong email and a wrong password take the same time.
        password_ok = await verify_password(owner_hash if email_ok else dummy_hash(), password)
        if not (email_ok and password_ok):
            raise AuthError(401, "invalid credentials")
        await store.record_login_success()
        return await self._start_session(store, uuid4())

    async def refresh(self, refresh_token: str | None) -> IssuedSession:
        store, _, _ = self._ready()
        if not refresh_token:
            raise NOT_AUTHENTICATED
        try:
            old = self.issuer.verify(refresh_token, "refresh")
        except TokenError:
            raise NOT_AUTHENTICATED from None
        access, _ = self.issuer.issue("access", old.sid)
        refresh, claims = self.issuer.issue("refresh", old.sid)
        outcome = await store.rotate_refresh(
            old.jti, old.sid, claims.jti, self._clock(), claims.expires_at
        )
        if outcome is not RotateOutcome.ROTATED:
            raise NOT_AUTHENTICATED
        return IssuedSession(old.sid, access, refresh, self.issuer.csrf_token(old.sid))

    async def logout(self, refresh_token: str | None, access_token: str | None) -> None:
        """End the session(s) the cookies belong to. Expired tokens still identify their session."""
        if self.store is None:
            return
        sessions = self.session_ids((refresh_token, "refresh"), (access_token, "access"))
        for sid in sessions:
            await self.store.revoke_session(sid, self._clock())

    def authenticate(self, access_token: str | None) -> Owner:
        if not access_token or self._owner_email is None:
            raise NOT_AUTHENTICATED
        try:
            claims = self.issuer.verify(access_token, "access")
        except TokenError:
            raise NOT_AUTHENTICATED from None
        return Owner(claims.sid, self._owner_email)

    def session_ids(self, *tokens: tuple[str | None, TokenType]) -> set[UUID]:
        """Sessions named by validly signed tokens (expired ones included), for CSRF checks."""
        found = set()
        for token, typ in tokens:
            if not token:
                continue
            try:
                found.add(self.issuer.verify(token, typ, allow_expired=True).sid)
            except TokenError:
                continue
        return found

    async def _start_session(self, store: AuthStore, sid: UUID) -> IssuedSession:
        access, _ = self.issuer.issue("access", sid)
        refresh, claims = self.issuer.issue("refresh", sid)
        await store.issue_refresh(claims.jti, sid, claims.issued_at, claims.expires_at)
        return IssuedSession(sid, access, refresh, self.issuer.csrf_token(sid))
