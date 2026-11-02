from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from agent_runtime.settings import RuntimeSettings, get_settings


class RuntimeDatabaseUnavailable(RuntimeError):
    pass


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: RuntimeSettings | None = None) -> AsyncEngine:
    global _engine, _session_factory
    if _engine is not None:
        return _engine

    active_settings = settings or get_settings()
    if not active_settings.database_url.startswith(("postgresql+asyncpg://", "postgresql+psycopg://", "postgresql://")):
        raise RuntimeDatabaseUnavailable("A PostgreSQL DATABASE_URL is required")

    _engine = create_async_engine(
        active_settings.database_url,
        pool_pre_ping=True,
        pool_recycle=300,
    )
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        get_engine()
    if _session_factory is None:  # pragma: no cover - defensive invariant
        raise RuntimeDatabaseUnavailable("Database session factory is unavailable")
    return _session_factory


@asynccontextmanager
async def scoped_session(tenant_id: str, workspace_id: str) -> AsyncIterator[AsyncSession]:
    """Open one short transaction and bind PostgreSQL RLS context locally."""

    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text("SELECT set_config('app.workspace_id', :workspace_id, true)"),
                {"workspace_id": workspace_id},
            )
            yield session

