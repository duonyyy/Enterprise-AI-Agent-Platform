from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from platform_common.rls import set_rls_context
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agent_runtime.settings import RuntimeSettings, get_settings

__all__ = [
    "RuntimeDatabaseUnavailable",
    "get_db_session",
    "get_engine",
    "get_session_factory",
    "scoped_session",
    "set_rls_context",
]


class RuntimeDatabaseUnavailable(RuntimeError):
    pass


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: RuntimeSettings | None = None) -> AsyncEngine:
    global _engine, _session_factory
    if _engine is not None:
        return _engine

    active_settings = settings or get_settings()
    if not active_settings.database_url.startswith(
        ("postgresql+asyncpg://", "postgresql+psycopg://", "postgresql://")
    ):
        raise RuntimeDatabaseUnavailable("A PostgreSQL DATABASE_URL is required")

    _engine = create_async_engine(
        active_settings.database_url,
        pool_pre_ping=True,
        pool_recycle=300,
    )
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        get_engine()
    if _session_factory is None:  # pragma: no cover - defensive invariant
        raise RuntimeDatabaseUnavailable("Database session factory is unavailable")
    return _session_factory


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession inside a transaction for request dependencies."""
    factory = get_session_factory()
    async with factory() as session, session.begin():
        yield session


@asynccontextmanager
async def scoped_session(
    tenant_id: str,
    workspace_id: str,
    user_id: str | None = None,
) -> AsyncIterator[AsyncSession]:
    """Open one short transaction and bind PostgreSQL RLS context locally."""

    factory = get_session_factory()
    async with factory() as session, session.begin():
        await set_rls_context(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        yield session
