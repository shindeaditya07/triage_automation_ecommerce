"""ReplyDesk API. Endpoints arrive branch by branch; so far: health, owner authentication, the
background job runner, ticket intake and the grounding warning for an owner's edit."""

import logging
from collections.abc import AsyncIterator, Mapping
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
from services.api.erasure.routes import router as erasure_router
from services.api.erasure.service import ErasureService
from services.api.erasure.store import ErasureStore
from services.api.grounding.routes import EditContextSource, EditValidationRuntime
from services.api.grounding.routes import router as grounding_router
from services.api.health import Database, check_readiness, open_database
from services.api.intake.routes import IntakeRuntime
from services.api.intake.routes import router as intake_router
from services.api.intake.service import IntakeService
from services.api.intake.store import PostgresTicketStore
from services.api.jobs.runner import JobRunner
from services.api.jobs.store import JobStore
from services.api.jobs.worker import Handler
from services.api.metrics.routes import MetricsReader
from services.api.metrics.routes import router as metrics_router
from services.api.metrics.store import MetricsStore
from services.api.models.enums import JobType
from services.api.pipeline.store import PipelineStore
from services.api.pipeline.wiring import PipelineRuntime, build_pipeline
from services.api.policy_source import loaded_passages
from services.api.retention.handler import make_retention_handler
from services.api.retention.store import RetentionStore
from services.api.retry.routes import router as retry_router
from services.api.retry.service import RetryService
from services.api.retry.store import RetryStore
from services.api.review.routes import router as review_router
from services.api.review.service import ReviewService
from services.api.review.store import ReviewStore
from services.api.security_headers import SecurityHeadersMiddleware
from services.api.settings import AuthSettings, IntakeSettings, JobSettings, StoreProfileSettings
from services.api.tickets.routes import TicketReader
from services.api.tickets.routes import router as tickets_router
from services.api.tickets.store import TicketReadStore

log = logging.getLogger("replydesk.api")


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


async def _start_jobs(
    database: Database,
    settings: JobSettings | None,
    handlers: Mapping[JobType, Handler],
    clock: Clock,
    supplied_engine: AsyncEngine | None,
) -> JobRunner | None:
    if database.engine is None or (settings is None and supplied_engine is not None):
        return None
    settings = settings or JobSettings()
    if not settings.job_worker_enabled:
        return None
    runner = JobRunner(JobStore(database.engine), handlers, settings, clock)
    await runner.start()
    return runner


def _build_intake(
    settings: IntakeSettings | None, database: Database, service: IntakeService | None, clock: Clock
) -> IntakeRuntime | None:
    """Intake needs a database. Its own settings all have defaults; a bad value turns intake off
    (503) rather than the whole API, and the log names the setting, never its value."""
    if settings is None:
        try:
            settings = IntakeSettings()
        except ValidationError as exc:
            log.error("intake disabled: invalid settings %s", [e["loc"] for e in exc.errors()])
            return None
    if service is None:
        if database.engine is None:
            return None
        service = IntakeService(
            PostgresTicketStore(database.engine, JobStore(database.engine)), clock
        )
    return IntakeRuntime.create(service, settings, clock)


def _build_edit_validation(
    database: Database, source: EditContextSource | None
) -> EditValidationRuntime | None:
    """Edit validation reads the ticket's draft, so it needs a database (or a test's source)."""
    if source is None:
        if database.engine is None:
            return None
        source = PipelineStore(database.engine)
    return EditValidationRuntime(source, StoreProfileSettings().store_sign_off, loaded_passages())


def create_app(
    engine: AsyncEngine | None = None,
    *,
    auth_settings: AuthSettings | None = None,
    auth_store: AuthStore | None = None,
    clock: Clock = utc_now,
    job_settings: JobSettings | None = None,
    job_handlers: Mapping[JobType, Handler] | None = None,
    intake_settings: IntakeSettings | None = None,
    intake_service: IntakeService | None = None,
    edit_context_source: EditContextSource | None = None,
    ticket_reader: TicketReader | None = None,
    metrics_reader: MetricsReader | None = None,
    review_service: ReviewService | None = None,
    erasure_service: ErasureService | None = None,
    retry_service: RetryService | None = None,
) -> FastAPI:
    """Build the app. Tests pass an engine, auth settings, a store and a clock; otherwise the
    database and auth settings come from the environment.

    Ticket intake needs a database too; a test can pass `intake_service` (and settings) instead.
    The job worker and sweeper run inside the app when there is a database, with the ticket
    pipeline registered when the LLM settings are in the environment. A test that passes
    an engine gets none unless it also passes `job_settings`.

    The app starts and listens whether or not the database is reachable (docs/AGENTS.md 4.2):
    connections open on first use, and `/readyz` reports what is wrong until they succeed.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(engine=engine, owned=False) if engine is not None else open_database()
        app.state.database = database
        app.state.auth = _build_auth(auth_settings, database, auth_store, clock)
        app.state.intake = _build_intake(intake_settings, database, intake_service, clock)
        app.state.edit_validation = _build_edit_validation(database, edit_context_source)
        app.state.clock = clock
        app.state.metrics_reader = metrics_reader or (
            MetricsStore(database.engine) if database.engine else None
        )
        app.state.ticket_reader = ticket_reader or (
            TicketReadStore(database.engine, loaded_passages()) if database.engine else None
        )
        pipeline: PipelineRuntime | None = None
        handlers = job_handlers
        if handlers is None:
            # The real pipeline only on the environment's own database; a test that passes an
            # engine or handlers keeps control of both.
            if engine is None and database.engine is not None:
                pipeline = build_pipeline(database.engine, clock)
            handlers = dict(pipeline.handlers) if pipeline else {}
            if engine is None and database.engine is not None:
                handlers[JobType.RETENTION_SWEEP] = make_retention_handler(
                    RetentionStore(database.engine),
                    (job_settings or JobSettings()).retention_days,
                    clock,
                )
        app.state.review = review_service or (
            ReviewService(
                ReviewStore(database.engine),
                clock,
                StoreProfileSettings().store_sign_off,
                loaded_passages(),
            )
            if database.engine
            else None
        )
        app.state.erasure = erasure_service or (
            ErasureService(ErasureStore(database.engine), clock) if database.engine else None
        )
        app.state.retry = retry_service or (
            RetryService(RetryStore(database.engine, JobStore(database.engine)), clock)
            if database.engine
            else None
        )
        runner = await _start_jobs(database, job_settings, handlers, clock, engine)
        try:
            yield
        finally:
            if runner is not None:
                await runner.stop()
            if pipeline is not None:
                await pipeline.aclose()
            await database.close()  # drains the pool on SIGTERM

    app = FastAPI(title="ReplyDesk API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)  # outermost: covers every response, errors too
    app.include_router(auth_router)
    app.include_router(intake_router)
    app.include_router(grounding_router)
    app.include_router(tickets_router)
    app.include_router(metrics_router)
    app.include_router(review_router)
    app.include_router(erasure_router)
    app.include_router(retry_router)

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
