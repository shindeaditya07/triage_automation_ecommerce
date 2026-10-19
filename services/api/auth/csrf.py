"""CSRF protection for every cookie-authenticated mutation (double-submit, bound to the session).

A browser attaches cookies to any request to our origin, including one a hostile page triggers.
So an unsafe request that carries a session cookie must also carry an `X-CSRF-Token` header that
equals the `rd_csrf` cookie *and* is the token for that session. Another site can make the browser
send the cookies but cannot read the token to put it in the header.

This is middleware, not a per-route dependency, so a route added later cannot forget it. Requests
with no valid session token pass through: there is nothing authenticated to ride, and the route's
own authentication will refuse them.
"""

from collections.abc import MutableMapping
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from services.api.auth.service import AuthService

ACCESS_COOKIE = "rd_access"
REFRESH_COOKIE = "rd_refresh"
CSRF_COOKIE = "rd_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
PROTECTED_PREFIX = "/api/"
# Login has no session yet: it is authenticated by the credentials in its body.
EXEMPT = frozenset({("POST", "/api/v1/auth/login")})


class CsrfMiddleware:
    def __init__(self, app: ASGIApp, exempt: frozenset[tuple[str, str]] = EXEMPT) -> None:
        self.app = app
        self.exempt = exempt

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if not self._needs_check(scope):
            await self.app(scope, receive, send)
            return
        service: AuthService | None = getattr(scope["app"].state, "auth", None)
        if service is None:
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        sessions = service.session_ids(
            (request.cookies.get(ACCESS_COOKIE), "access"),
            (request.cookies.get(REFRESH_COOKIE), "refresh"),
        )
        header = request.headers.get(CSRF_HEADER)
        cookie = request.cookies.get(CSRF_COOKIE)
        if sessions and not all(
            service.issuer.csrf_matches(sid, header, cookie) for sid in sessions
        ):
            response = JSONResponse({"detail": "CSRF token missing or invalid"}, status_code=403)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _needs_check(self, scope: MutableMapping[str, Any]) -> bool:
        return (
            scope["type"] == "http"
            and scope["method"] not in SAFE_METHODS
            and scope["path"].startswith(PROTECTED_PREFIX)
            and (scope["method"], scope["path"]) not in self.exempt
        )
