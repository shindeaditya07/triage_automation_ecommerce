"""Session tokens and CSRF tokens.

Access and refresh tokens are HS256 JWTs. Both carry a session id (`sid`) that stays the same
across refreshes, a unique `jti`, and a `typ` so one kind cannot be presented as the other.
Expiry is checked here against the injected clock, not by PyJWT, so tests control time.

The CSRF token is an HMAC of the session id under a key derived separately from the signing key,
so it can be recomputed from the session cookie alone and never stored (a signed double-submit
token: the header must equal the cookie, and both must be the HMAC for this session).
"""

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

import jwt

from services.api.clock import Clock

ALGORITHM = "HS256"
SUBJECT = "owner"
CLOCK_SKEW = timedelta(seconds=60)
TokenType = Literal["access", "refresh"]


class TokenError(Exception):
    """The token is unusable. The reason is for logs and tests, never for the caller."""


@dataclass(frozen=True)
class Claims:
    sid: UUID
    jti: UUID
    typ: TokenType
    issued_at: datetime
    expires_at: datetime


def _subkey(secret: str, label: bytes) -> bytes:
    return hmac.new(secret.encode(), b"replydesk:" + label, hashlib.sha256).digest()


class TokenIssuer:
    def __init__(
        self, secret: str, clock: Clock, access_ttl: timedelta, refresh_ttl: timedelta
    ) -> None:
        self._jwt_key = _subkey(secret, b"jwt")
        self._csrf_key = _subkey(secret, b"csrf")
        self._clock = clock
        self.access_ttl = access_ttl
        self.refresh_ttl = refresh_ttl

    def issue(self, typ: TokenType, sid: UUID, jti: UUID | None = None) -> tuple[str, Claims]:
        now = self._clock()
        ttl = self.access_ttl if typ == "access" else self.refresh_ttl
        claims = Claims(sid=sid, jti=jti or uuid4(), typ=typ, issued_at=now, expires_at=now + ttl)
        payload = {
            "sub": SUBJECT,
            "sid": str(claims.sid),
            "jti": str(claims.jti),
            "typ": typ,
            "iat": int(claims.issued_at.timestamp()),
            "exp": int(claims.expires_at.timestamp()),
        }
        return jwt.encode(payload, self._jwt_key, algorithm=ALGORITHM), claims

    def verify(self, token: str, typ: TokenType, *, allow_expired: bool = False) -> Claims:
        try:
            payload = jwt.decode(
                token,
                self._jwt_key,
                algorithms=[ALGORITHM],  # pinned: never trust the token's own "alg"
                options={
                    "require": ["exp", "iat", "sub", "jti", "sid", "typ"],
                    "verify_exp": False,
                    "verify_iat": False,
                },
            )
            if payload["sub"] != SUBJECT or payload["typ"] != typ:
                raise TokenError("wrong subject or token type")
            claims = Claims(
                sid=UUID(payload["sid"]),
                jti=UUID(payload["jti"]),
                typ=typ,
                issued_at=_from_timestamp(payload["iat"], self._clock()),
                expires_at=_from_timestamp(payload["exp"], self._clock()),
            )
        except (jwt.PyJWTError, KeyError, ValueError, TypeError, OverflowError) as exc:
            raise TokenError(type(exc).__name__) from exc
        now = self._clock()
        if claims.issued_at > now + CLOCK_SKEW:
            raise TokenError("issued in the future")
        if not allow_expired and claims.expires_at <= now:
            raise TokenError("expired")
        return claims

    def csrf_token(self, sid: UUID) -> str:
        digest = hmac.new(self._csrf_key, str(sid).encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def csrf_matches(self, sid: UUID, *candidates: str | None) -> bool:
        """True when every candidate is present and equals the token for this session."""
        expected = self.csrf_token(sid)
        return all(
            candidate is not None and secrets.compare_digest(candidate, expected)
            for candidate in candidates
        )


def _from_timestamp(value: object, reference: datetime) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("timestamp must be an integer")
    return datetime.fromtimestamp(value, tz=reference.tzinfo)
