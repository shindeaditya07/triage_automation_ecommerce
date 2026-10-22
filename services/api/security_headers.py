"""Security headers on every API response (docs/AGENTS.md section 7).

`nosniff`, `X-Frame-Options: DENY` and a strict referrer policy go on everything. The API serves
JSON only, so it also gets a CSP that allows nothing and forbids framing. The interactive docs
(`/docs`, `/redoc`) load scripts and styles from a CDN, so they keep the three headers above but
not that CSP; a route the page itself already set is left as the route wrote it.

This is middleware, not a per-route dependency, so a route added later cannot forget the headers.
The web app has its own CSP, set by the web server (apps/web/nginx.conf, or the Render Static
Site's Headers settings: guides/security-headers.md).
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
DOCS_PATHS = frozenset({"/docs", "/redoc", "/docs/oauth2-redirect"})

ALWAYS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        wanted = dict(ALWAYS)
        if scope["path"] not in DOCS_PATHS:
            wanted["Content-Security-Policy"] = API_CSP

        async def add_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in wanted.items():
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, add_headers)
