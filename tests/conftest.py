import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from mabeyn.core.config import get_settings
from mabeyn.core.loop import use_selector_loop_on_windows
from mabeyn.db.provisioning import create_tenant
from tests.factories import SeededTenant, delete_tenant, seed_tenant

use_selector_loop_on_windows()


@pytest_asyncio.fixture(scope="session")
async def admin_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().admin_database_url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except (OSError, SQLAlchemyError):
        await engine.dispose()
        pytest.skip("database is not available")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def tenant_a(admin_engine: AsyncEngine) -> AsyncIterator[SeededTenant]:
    seeded = await seed_tenant(admin_engine, "A")
    yield seeded
    await delete_tenant(admin_engine, seeded.id)


@pytest_asyncio.fixture(scope="session")
async def tenant_b(admin_engine: AsyncEngine) -> AsyncIterator[SeededTenant]:
    seeded = await seed_tenant(admin_engine, "B")
    yield seeded
    await delete_tenant(admin_engine, seeded.id)


@pytest_asyncio.fixture
async def fresh_tenant(admin_engine: AsyncEngine) -> AsyncIterator[uuid.UUID]:
    suffix = uuid.uuid4().hex[:8]
    tenant_id = await create_tenant(
        admin_engine, "Fresh", f"fresh-{suffix}", f"fresh-{suffix}@example.com", "a-long-password-1"
    )
    yield tenant_id
    await delete_tenant(admin_engine, tenant_id)


@pytest_asyncio.fixture
async def tenant_x(admin_engine: AsyncEngine) -> AsyncIterator[SeededTenant]:
    seeded = await seed_tenant(admin_engine, "X")
    yield seeded
    await delete_tenant(admin_engine, seeded.id)


@pytest_asyncio.fixture
async def tenant_y(admin_engine: AsyncEngine) -> AsyncIterator[SeededTenant]:
    seeded = await seed_tenant(admin_engine, "Y")
    yield seeded
    await delete_tenant(admin_engine, seeded.id)


@pytest_asyncio.fixture(scope="session")
async def checkpointer(admin_engine: AsyncEngine):
    from mabeyn.agents.checkpoint import open_checkpointer, setup_checkpoint_tables

    await setup_checkpoint_tables()
    async with open_checkpointer() as saver:
        yield saver
