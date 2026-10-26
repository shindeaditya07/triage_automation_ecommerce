import base64
import json
from datetime import timedelta
from uuid import uuid4

import jwt
import pytest

from services.api.auth.tokens import TokenError, TokenIssuer
from tests.auth.conftest import SECRET_KEY, FakeClock


@pytest.fixture
def issuer(clock: FakeClock) -> TokenIssuer:
    return TokenIssuer(SECRET_KEY, clock, timedelta(minutes=15), timedelta(days=7))


def test_round_trip_carries_session_and_expiry(issuer: TokenIssuer, clock: FakeClock) -> None:
    sid = uuid4()
    token, claims = issuer.issue("access", sid)
    verified = issuer.verify(token, "access")
    assert (verified.sid, verified.jti, verified.typ) == (sid, claims.jti, "access")
    assert verified.expires_at - verified.issued_at == timedelta(minutes=15)


def test_refresh_lives_for_the_refresh_ttl(issuer: TokenIssuer) -> None:
    token, _ = issuer.issue("refresh", uuid4())
    claims = issuer.verify(token, "refresh")
    assert claims.expires_at - claims.issued_at == timedelta(days=7)


def test_access_token_expires_after_fifteen_minutes(issuer: TokenIssuer, clock: FakeClock) -> None:
    token, _ = issuer.issue("access", uuid4())
    clock.advance(minutes=14, seconds=59)
    issuer.verify(token, "access")
    clock.advance(seconds=1)
    with pytest.raises(TokenError, match="expired"):
        issuer.verify(token, "access")


def test_an_expired_token_still_names_its_session_when_asked(
    issuer: TokenIssuer, clock: FakeClock
) -> None:
    sid = uuid4()
    token, _ = issuer.issue("access", sid)
    clock.advance(days=30)
    assert issuer.verify(token, "access", allow_expired=True).sid == sid


def test_a_token_is_not_accepted_as_the_other_type(issuer: TokenIssuer) -> None:
    access, _ = issuer.issue("access", uuid4())
    refresh, _ = issuer.issue("refresh", uuid4())
    with pytest.raises(TokenError):
        issuer.verify(access, "refresh")
    with pytest.raises(TokenError):
        issuer.verify(refresh, "access")


def test_a_token_signed_with_another_secret_is_rejected(
    issuer: TokenIssuer, clock: FakeClock
) -> None:
    other = TokenIssuer("z" * 40, clock, timedelta(minutes=15), timedelta(days=7))
    token, _ = other.issue("access", uuid4())
    with pytest.raises(TokenError):
        issuer.verify(token, "access")


def test_the_unsigned_alg_none_token_is_rejected(issuer: TokenIssuer, clock: FakeClock) -> None:
    def b64(data: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    now = int(clock().timestamp())
    payload = {
        "sub": "owner", "sid": str(uuid4()), "jti": str(uuid4()), "typ": "access",
        "iat": now, "exp": now + 900,
    }  # fmt: skip
    forged = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(payload)}."
    with pytest.raises(TokenError):
        issuer.verify(forged, "access")


def test_a_token_signed_with_a_different_algorithm_is_rejected(
    issuer: TokenIssuer, clock: FakeClock
) -> None:
    now = int(clock().timestamp())
    payload = {
        "sub": "owner", "sid": str(uuid4()), "jti": str(uuid4()), "typ": "access",
        "iat": now, "exp": now + 900,
    }  # fmt: skip
    forged = jwt.encode(payload, SECRET_KEY * 2, algorithm="HS512")
    with pytest.raises(TokenError):
        issuer.verify(forged, "access")


@pytest.mark.parametrize("missing", ["exp", "iat", "sub", "jti", "sid", "typ"])
def test_every_claim_is_required(issuer: TokenIssuer, clock: FakeClock, missing: str) -> None:
    token, _ = issuer.issue("access", uuid4())
    payload = jwt.decode(token, options={"verify_signature": False})
    del payload[missing]
    # Re-sign with the issuer's own key so only the missing claim can be the reason.
    resigned = jwt.encode(payload, issuer._jwt_key, algorithm="HS256")
    with pytest.raises(TokenError):
        issuer.verify(resigned, "access")


@pytest.mark.parametrize(
    ("claim", "value"),
    [("sub", "someone-else"), ("sid", "not-a-uuid"), ("exp", "tomorrow"), ("iat", True)],
)
def test_malformed_claim_values_are_rejected(
    issuer: TokenIssuer, claim: str, value: object
) -> None:
    token, _ = issuer.issue("access", uuid4())
    payload = jwt.decode(token, options={"verify_signature": False})
    payload[claim] = value
    with pytest.raises(TokenError):
        issuer.verify(jwt.encode(payload, issuer._jwt_key, algorithm="HS256"), "access")


def test_a_token_issued_in_the_future_is_rejected(issuer: TokenIssuer, clock: FakeClock) -> None:
    token, _ = issuer.issue("access", uuid4())
    clock.advance(minutes=-5)
    with pytest.raises(TokenError, match="future"):
        issuer.verify(token, "access")


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "....", "x" * 5000])
def test_garbage_is_rejected(issuer: TokenIssuer, garbage: str) -> None:
    with pytest.raises(TokenError):
        issuer.verify(garbage, "access")


def test_csrf_token_is_stable_per_session_and_differs_between_sessions(
    issuer: TokenIssuer,
) -> None:
    sid, other = uuid4(), uuid4()
    assert issuer.csrf_token(sid) == issuer.csrf_token(sid)
    assert issuer.csrf_token(sid) != issuer.csrf_token(other)


def test_csrf_matches_needs_every_candidate_to_be_present_and_right(issuer: TokenIssuer) -> None:
    sid = uuid4()
    good = issuer.csrf_token(sid)
    assert issuer.csrf_matches(sid, good, good)
    assert not issuer.csrf_matches(sid, good, None)
    assert not issuer.csrf_matches(sid, None, None)
    assert not issuer.csrf_matches(sid, good, "wrong")
    assert not issuer.csrf_matches(sid, issuer.csrf_token(uuid4()), good)


def test_csrf_key_is_not_the_signing_key(issuer: TokenIssuer) -> None:
    assert issuer._csrf_key != issuer._jwt_key
