"""`/api/v1/auth/*` and the `require_owner` dependency every owner-only route uses."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field

from services.api.auth.csrf import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from services.api.auth.passwords import MAX_PASSWORD_LENGTH
from services.api.auth.ratelimit import client_ip
from services.api.auth.service import AuthError, AuthService, IssuedSession, Owner
from services.api.models.common import StrictModel

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

ACCESS_PATH = "/api"
REFRESH_PATH = "/api/v1/auth"
NO_STORE = {"Cache-Control": "no-store"}


class LoginRequest(StrictModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


def get_auth(request: Request) -> AuthService:
    service: AuthService | None = getattr(request.app.state, "auth", None)
    if service is None:
        raise HTTPException(503, "authentication is not available")
    return service


def require_owner(request: Request, service: Annotated[AuthService, Depends(get_auth)]) -> Owner:
    """Dependency for owner-only routes: a valid, unexpired access cookie, else 401."""
    try:
        return service.authenticate(request.cookies.get(ACCESS_COOKIE))
    except AuthError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


def _set_cookies(response: Response, service: AuthService, session: IssuedSession) -> None:
    settings = service.settings
    secure = settings.cookie_secure
    response.set_cookie(
        ACCESS_COOKIE,
        session.access_token,
        max_age=int(service.issuer.access_ttl.total_seconds()),
        path=ACCESS_PATH,
        httponly=True,
        secure=secure,
        samesite="strict",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        session.refresh_token,
        max_age=int(service.issuer.refresh_ttl.total_seconds()),
        path=REFRESH_PATH,
        httponly=True,
        secure=secure,
        samesite="strict",
    )
    # Readable by the page's JavaScript on purpose: it echoes this value in X-CSRF-Token.
    response.set_cookie(
        CSRF_COOKIE,
        session.csrf_token,
        max_age=int(service.issuer.refresh_ttl.total_seconds()),
        path="/",
        httponly=False,
        secure=secure,
        samesite="strict",
    )


def _clear_cookies(response: Response, service: AuthService) -> None:
    secure = service.settings.cookie_secure
    for name, path, httponly in (
        (ACCESS_COOKIE, ACCESS_PATH, True),
        (REFRESH_COOKIE, REFRESH_PATH, True),
        (CSRF_COOKIE, "/", False),
    ):
        response.delete_cookie(name, path=path, httponly=httponly, secure=secure, samesite="strict")


def _session_body(service: AuthService, session: IssuedSession) -> dict[str, object]:
    return {
        "csrf_token": session.csrf_token,
        "access_expires_in": int(service.issuer.access_ttl.total_seconds()),
    }


def _error(exc: AuthError) -> JSONResponse:
    headers = dict(NO_STORE)
    if exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=headers)


@router.post("/login")
async def login(
    body: LoginRequest, request: Request, service: Annotated[AuthService, Depends(get_auth)]
) -> Response:
    peer = request.client.host if request.client else None
    ip = client_ip(request.headers, peer, service.settings.trusted_proxy_hops)
    try:
        session = await service.login(body.email, body.password, ip)
    except AuthError as exc:
        return _error(exc)
    response = JSONResponse(_session_body(service, session), headers=NO_STORE)
    _set_cookies(response, service, session)
    return response


@router.post("/refresh")
async def refresh(request: Request, service: Annotated[AuthService, Depends(get_auth)]) -> Response:
    try:
        session = await service.refresh(request.cookies.get(REFRESH_COOKIE))
    except AuthError as exc:
        response = _error(exc)
        if exc.status_code == 401:
            _clear_cookies(response, service)
        return response
    response = JSONResponse(_session_body(service, session), headers=NO_STORE)
    _set_cookies(response, service, session)
    return response


@router.post("/logout")
async def logout(request: Request, service: Annotated[AuthService, Depends(get_auth)]) -> Response:
    """Always clears the cookies. If revoking the session fails, say so rather than pretend."""
    failure: AuthError | None = None
    try:
        await service.logout(
            request.cookies.get(REFRESH_COOKIE), request.cookies.get(ACCESS_COOKIE)
        )
    except Exception:
        failure = AuthError(503, "could not end the session on the server; try again")
    response = _error(failure) if failure else Response(status_code=204, headers=NO_STORE)
    _clear_cookies(response, service)
    return response


@router.get("/me")
async def me(owner: Annotated[Owner, Depends(require_owner)]) -> JSONResponse:
    return JSONResponse({"email": owner.email}, headers=NO_STORE)
