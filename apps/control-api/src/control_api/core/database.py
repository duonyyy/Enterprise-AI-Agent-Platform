from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from platform_common.rls import set_rls_context
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from control_api.core.settings import settings

Base = declarative_base()

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_timeout=settings.database_pool_timeout_seconds,
    connect_args={"server_settings": {"application_name": "enterprise-control-api"}},
)

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def scoped_session(
    tenant_id: str,
    workspace_id: str,
    user_id: str | None = None,
) -> AsyncIterator[AsyncSession]:
    """Open a transaction-scoped AsyncSession with fail-closed RLS variables bound."""
    async with AsyncSessionLocal() as session, session.begin():
        await set_rls_context(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        yield session


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session, session.begin():
        yield session

