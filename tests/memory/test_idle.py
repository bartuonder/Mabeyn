import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabeyn.memory.idle import extract_idle, idle_conversations
from mabeyn.memory.service import MemoryService
from mabeyn.memory.store import MemoryStore
from tests.fakes import FakeLLM
from tests.memory.test_extract import conversation_with

ALL = 100_000  # a shared development database may hold many other idle conversations
PREFERENCE = {"category": "preference", "slug": "carrier", "text": "Prefers Aras Kargo."}


def service(store: MemoryStore, *items: dict[str, str]) -> MemoryService:
    payload = json.dumps({"memories": list(items)})
    return MemoryService(FakeLLM(lambda messages, model: payload), store)


async def age(engine: AsyncEngine, conversation_id: uuid.UUID, minutes: int) -> None:
    moment = datetime.now(UTC) - timedelta(minutes=minutes)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE messages SET created_at = :t WHERE conversation_id = :c"),
            {"t": moment, "c": conversation_id},
        )


async def test_idle_conversations_are_mined_once(
    fresh_tenant, admin_engine: AsyncEngine, memory_store: MemoryStore
):
    old = await conversation_with(fresh_tenant, "cust-1", ("user", "I prefer Aras Kargo."))
    recent = await conversation_with(fresh_tenant, "cust-2", ("user", "I prefer Aras Kargo."))
    await age(admin_engine, old, 45)

    found = {c for _, c in await idle_conversations(limit=ALL)}
    assert old in found and recent not in found

    mining = service(memory_store, PREFERENCE)
    assert await extract_idle(mining, limit=ALL) >= 1
    assert [n.text for n in await memory_store.list(fresh_tenant, "cust-1")] == [
        "Prefers Aras Kargo."
    ]
    assert await memory_store.list(fresh_tenant, "cust-2") == []
    assert old not in {c for _, c in await idle_conversations(limit=ALL)}
    await memory_store.forget(fresh_tenant, "cust-1")


async def test_idle_mining_covers_every_tenant(
    fresh_tenant, tenant_x, tenant_y, admin_engine: AsyncEngine, memory_store: MemoryStore
):
    first = await conversation_with(tenant_x.id, "cust-1", ("user", "I prefer Aras Kargo."))
    second = await conversation_with(tenant_y.id, "cust-1", ("user", "I prefer Aras Kargo."))
    await age(admin_engine, first, 60)
    await age(admin_engine, second, 60)

    await extract_idle(service(memory_store, PREFERENCE), limit=ALL)
    assert await memory_store.list(tenant_x.id, "cust-1")
    assert await memory_store.list(tenant_y.id, "cust-1")
    await memory_store.forget(tenant_x.id, "cust-1")
    await memory_store.forget(tenant_y.id, "cust-1")


async def test_a_failed_extraction_is_retried_on_the_next_run(
    fresh_tenant, admin_engine: AsyncEngine, memory_store: MemoryStore
):
    conversation = await conversation_with(fresh_tenant, "cust-1", ("user", "I prefer Aras."))
    await age(admin_engine, conversation, 60)

    def explode(messages, model):
        raise ConnectionError("model is down")

    await extract_idle(MemoryService(FakeLLM(explode), memory_store), limit=ALL)
    assert conversation in {c for _, c in await idle_conversations(limit=ALL)}
    await extract_idle(service(memory_store), limit=ALL)
    assert conversation not in {c for _, c in await idle_conversations(limit=ALL)}
