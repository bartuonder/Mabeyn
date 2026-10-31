import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mabeyn.core.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


@asynccontextmanager
async def tenant_session(tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """Open a transaction with `app.tenant_id` set so row-level security applies."""
    async with get_sessionmaker()() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
        yield session


@asynccontextmanager
async def anonymous_session() -> AsyncIterator[AsyncSession]:
    """Session without tenant context; tenant tables return no rows."""
    async with get_sessionmaker()() as session, session.begin():
        yield session
