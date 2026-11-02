import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabeyn.memory.schema import MemoryCategory, MemoryRecord
from mabeyn.memory.store import MemoryStore, namespace


def rec(category: str, slug: str, body: str) -> MemoryRecord:
    return MemoryRecord(category=category, slug=slug, text=body)


@pytest.fixture
def ids():
    return uuid.uuid4(), uuid.uuid4()


async def test_saved_memory_is_listed_newest_first(memory_store: MemoryStore, ids):
    tenant, _ = ids
    await memory_store.save(tenant, "c-1", [rec("preference", "carrier", "Prefers Aras Kargo.")])
    await memory_store.save(
        tenant, "c-1", [rec("issue", "damaged_item", "Reported a damaged item.")]
    )
    stored = await memory_store.list(tenant, "c-1")
    assert [m.category for m in stored] == [MemoryCategory.ISSUE, MemoryCategory.PREFERENCE]
    assert await memory_store.list(tenant, "c-1", limit=1) == stored[:1]
    await memory_store.forget(tenant, "c-1")


async def test_same_key_replaces_the_older_fact(memory_store: MemoryStore, ids):
    tenant, _ = ids
    await memory_store.save(tenant, "c-1", [rec("preference", "carrier", "Prefers Aras Kargo.")])
    await memory_store.save(tenant, "c-1", [rec("preference", "carrier", "Prefers MNG Kargo.")])
    stored = await memory_store.list(tenant, "c-1")
    assert [m.text for m in stored] == ["Prefers MNG Kargo."]
    await memory_store.forget(tenant, "c-1")


async def test_customers_and_tenants_are_separate(memory_store: MemoryStore, ids):
    tenant, other_tenant = ids
    await memory_store.save(tenant, "c-1", [rec("preference", "carrier", "Prefers Aras Kargo.")])
    assert await memory_store.list(tenant, "c-2") == []
    assert await memory_store.list(other_tenant, "c-1") == []
    assert await memory_store.forget(other_tenant, "c-1") == 0
    assert len(await memory_store.list(tenant, "c-1")) == 1
    await memory_store.forget(tenant, "c-1")


async def test_customer_ids_with_special_characters_stay_isolated(memory_store: MemoryStore, ids):
    tenant, _ = ids
    await memory_store.save(tenant, "a@b.com", [rec("context", "region", "Lives in KKTC.")])
    assert await memory_store.list(tenant, "a@b") == []
    assert await memory_store.list(tenant, "a@b.com")
    assert "." not in namespace(tenant, "a@b.com")[1]
    await memory_store.forget(tenant, "a@b.com")


async def test_forget_removes_everything_of_one_customer(memory_store: MemoryStore, ids):
    tenant, _ = ids
    await memory_store.save(
        tenant,
        "c-1",
        [rec("preference", "carrier", "Prefers Aras Kargo."), rec("context", "region", "KKTC.")],
    )
    await memory_store.save(tenant, "c-2", [rec("context", "region", "Lives in Ankara.")])
    assert await memory_store.forget(tenant, "c-1") == 2
    assert await memory_store.list(tenant, "c-1") == []
    assert len(await memory_store.list(tenant, "c-2")) == 1
    await memory_store.forget(tenant, "c-2")


async def test_size_cap_drops_the_oldest_records(memory_store: MemoryStore, ids, monkeypatch):
    tenant, _ = ids
    monkeypatch.setattr(memory_store, "max_records", 3)
    for index in range(5):
        await memory_store.save(
            tenant, "c-1", [rec("issue", f"issue_{index}", f"Issue number {index}.")]
        )
    stored = await memory_store.list(tenant, "c-1")
    assert [m.key for m in stored] == ["issue:issue_4", "issue:issue_3", "issue:issue_2"]
    await memory_store.forget(tenant, "c-1")


async def test_expired_memories_are_hidden_and_swept(
    memory_store: MemoryStore, admin_engine: AsyncEngine, ids
):
    tenant, _ = ids
    await memory_store.save(tenant, "c-1", [rec("preference", "carrier", "Prefers Aras Kargo.")])
    prefix = ".".join(namespace(tenant, "c-1"))
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("UPDATE store SET expires_at = now() - interval '1 minute' WHERE prefix = :p"),
            {"p": prefix},
        )
    assert await memory_store.list(tenant, "c-1") == []
    assert await memory_store.sweep() >= 1
    async with admin_engine.connect() as conn:
        left = await conn.scalar(
            text("SELECT count(*) FROM store WHERE prefix = :p"), {"p": prefix}
        )
    assert left == 0


async def test_records_get_a_time_to_live_from_settings(
    memory_store: MemoryStore, admin_engine: AsyncEngine, ids
):
    tenant, _ = ids
    await memory_store.save(tenant, "c-1", [rec("preference", "carrier", "Prefers Aras Kargo.")])
    prefix = ".".join(namespace(tenant, "c-1"))
    async with admin_engine.connect() as conn:
        days = await conn.scalar(
            text("SELECT extract(day FROM expires_at - now()) FROM store WHERE prefix = :p"),
            {"p": prefix},
        )
    assert 178 <= days <= 180
    await memory_store.forget(tenant, "c-1")
