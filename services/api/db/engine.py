"""Async engine construction. Creating an engine never connects, so the API can start with the
database down; connections open on first use and are re-tested before reuse."""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

CONNECT_TIMEOUT_SECONDS = 3
COMMAND_TIMEOUT_SECONDS = 5


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={
            "timeout": CONNECT_TIMEOUT_SECONDS,
            "command_timeout": COMMAND_TIMEOUT_SECONDS,
        },
    )
