# 0001. Concurrency style

- Status: accepted
- Date: 2026-10-19

## Context

`docs/AGENTS.md` requires one concurrency style, chosen once, with no blocking calls inside async
code. The branch plan sketched "async FastAPI with a thread pool for sync SQLAlchemy", but the
database layer was already built (feature/p1-db-schema) on SQLAlchemy 2.0 Core with the asyncpg
driver, and the health and auth code followed it.

## Decision

Async all the way down: FastAPI route handlers are `async def`, database access uses
`AsyncEngine`/`AsyncConnection` with asyncpg, and outbound HTTP will use an async client. Work that
is genuinely CPU-bound and blocking runs in a worker thread via `asyncio.to_thread`. Today that is
argon2 password verification (`services/api/auth/passwords.py`), which takes tens of milliseconds
and would otherwise stall every other request. Plain `def` handlers are allowed only for trivial,
non-blocking work (`/healthz`); FastAPI runs those in its own thread pool.

## Consequences

- One mental model: anything that waits (database, network, subprocess) is awaited.
- A new blocking library call in an `async def` is a bug. Review for it; wrap it in
  `asyncio.to_thread` if it cannot be avoided.
- Tests that touch the database use `asyncio.run` or the anyio plugin, and engines created for a
  test use `NullPool` so a connection is never reused across event loops.
- The thread-pool-for-sync-SQLAlchemy option in the branch plan is not used.
