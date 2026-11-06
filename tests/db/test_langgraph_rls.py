import uuid

import pytest
import pytest_asyncio
from psycopg.errors import InsufficientPrivilege
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabeyn.db.provisioning import create_tenant
from mabeyn.db.tenant_context import tenant_scope
from mabeyn.memory.schema import MemoryRecord
from mabeyn.memory.store import MemoryStore, namespace
from tests.agents.harness import Brain, build_env
from tests.factories import delete_tenant

NOTE = MemoryRecord(category="preference", slug="carrier", text="Prefers Aras Kargo.")


@pytest_asyncio.fixture
async def other_tenant(admin_engine: AsyncEngine):
    suffix = uuid.uuid4().hex[:8]
    tenant_id = await create_tenant(
        admin_engine, "Other", f"other-{suffix}", f"other-{suffix}@example.com", "a-long-password-1"
    )
    yield tenant_id
    await delete_tenant(admin_engine, tenant_id)


async def count(pool, table: str) -> int:
    async with pool.connection() as conn:
        cursor = await conn.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608
        return int((await cursor.fetchone())["n"])


async def test_memory_rows_are_invisible_to_other_tenants_and_to_no_tenant(
    fresh_tenant, other_tenant, memory_store: MemoryStore
):
    await memory_store.save(fresh_tenant, "customer-1", [NOTE])
    try:
        assert await count(memory_store.pool, "store") == 0
        with tenant_scope(other_tenant):
            assert await memory_store._search(namespace(fresh_tenant, "customer-1"), 10) == []
            assert await count(memory_store.pool, "store") == 0
        with tenant_scope(fresh_tenant):
            assert await count(memory_store.pool, "store") >= 1
        assert [m.text for m in await memory_store.list(fresh_tenant, "customer-1")] == [NOTE.text]
    finally:
        await memory_store.forget(fresh_tenant, "customer-1")


async def test_a_tenant_cannot_write_into_another_tenants_memory(
    fresh_tenant, other_tenant, memory_store: MemoryStore
):
    with tenant_scope(other_tenant), pytest.raises(InsufficientPrivilege):
        await memory_store._put(namespace(fresh_tenant, "customer-1"), "k", {"text": "planted"})


async def test_the_ttl_sweep_still_removes_expired_rows_of_every_tenant(
    fresh_tenant, memory_store: MemoryStore, admin_engine: AsyncEngine
):
    await memory_store.save(fresh_tenant, "customer-1", [NOTE])
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE store SET expires_at = now() - interval '1 hour' "
                "WHERE split_part(prefix, '.', 1) = :t"
            ),
            {"t": str(fresh_tenant)},
        )
    assert await memory_store.sweep() >= 1
    async with admin_engine.begin() as conn:
        remaining = await conn.scalar(
            text("SELECT count(*) FROM store WHERE split_part(prefix, '.', 1) = :t"),
            {"t": str(fresh_tenant)},
        )
    assert remaining == 0


async def test_checkpoints_follow_the_same_isolation(fresh_tenant, other_tenant, checkpointer):
    env = await build_env(fresh_tenant, checkpointer, Brain(route=["knowledge"]))
    await env.ask("Hello")
    pool = checkpointer.conn
    assert await count(pool, "checkpoints") == 0
    with tenant_scope(other_tenant):
        assert await count(pool, "checkpoints") == 0
    with tenant_scope(fresh_tenant):
        assert await count(pool, "checkpoints") >= 1
        assert await count(pool, "checkpoint_writes") >= 0
