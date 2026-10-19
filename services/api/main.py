"""ReplyDesk API. Endpoints arrive branch by branch; this one has the two health endpoints."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.health import Database, check_readiness, open_database


def create_app(engine: AsyncEngine | None = None) -> FastAPI:
    """Build the app. Tests pass an engine; otherwise the database comes from DATABASE_URL.

    The app starts and listens whether or not the database is reachable (docs/AGENTS.md 4.2):
    connections open on first use, and `/readyz` reports what is wrong until they succeed.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(engine=engine, owned=False) if engine is not None else open_database()
        app.state.database = database
        try:
            yield
        finally:
            await database.close()  # drains the pool on SIGTERM

    app = FastAPI(title="ReplyDesk API", version="0.1.0", lifespan=lifespan)

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
