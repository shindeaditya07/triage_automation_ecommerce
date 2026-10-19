"""ReplyDesk API. Endpoints arrive branch by branch; so far: health and owner authentication."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.auth.csrf import CsrfMiddleware
from services.api.auth.routes import router as auth_router
from services.api.auth.service import AuthService
from services.api.auth.store import AuthStore, PostgresAuthStore
from services.api.clock import Clock, utc_now
from services.api.health import Database, check_readiness, open_database
from services.api.settings import AuthSettings


def _build_auth(
    settings: AuthSettings | None, database: Database, store: AuthStore | None, clock: Clock
) -> AuthService | None:
    """Auth settings come from the environment; a host without them still starts, and the
    auth routes answer 503 until they are set."""
    if settings is None:
        try:
            settings = AuthSettings()  # type: ignore[call-arg]
        except ValidationError:
            return None
    if store is None and database.engine is not None:
        store = PostgresAuthStore(database.engine)
    return AuthService(settings, store, clock)


def create_app(
    engine: AsyncEngine | None = None,
    *,
    auth_settings: AuthSettings | None = None,
    auth_store: AuthStore | None = None,
    clock: Clock = utc_now,
) -> FastAPI:
    """Build the app. Tests pass an engine, auth settings, a store and a clock; otherwise the
    database and auth settings come from the environment.

    The app starts and listens whether or not the database is reachable (docs/AGENTS.md 4.2):
    connections open on first use, and `/readyz` reports what is wrong until they succeed.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(engine=engine, owned=False) if engine is not None else open_database()
        app.state.database = database
        app.state.auth = _build_auth(auth_settings, database, auth_store, clock)
        try:
            yield
        finally:
            await database.close()  # drains the pool on SIGTERM

    app = FastAPI(title="ReplyDesk API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CsrfMiddleware)
    app.include_router(auth_router)

    @app.exception_handler(SQLAlchemyError)
    @app.exception_handler(OSError)
    async def storage_unavailable(_: Request, __: Exception) -> JSONResponse:
        # A database outage is a 503, not a 500, and never echoes driver text (hosts, SQL).
        return JSONResponse({"detail": "service unavailable"}, status_code=503)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Liveness: the process is up. Never touches the database."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(request: Request) -> JSONResponse:
        """Readiness: database, pgvector, migrations at head, policy index at the current version"""
        readiness = await check_readiness(request.app.state.database)
        return JSONResponse(readiness.public(), status_code=200 if readiness.ready else 503)

    return app


app = create_app()
