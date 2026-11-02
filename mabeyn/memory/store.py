import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote

from langgraph.store.base import TTLConfig
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from mabeyn.agents.checkpoint import psycopg_dsn
from mabeyn.core.config import get_settings
from mabeyn.core.loop import use_selector_loop_on_windows
from mabeyn.memory.schema import MemoryCategory, MemoryRecord, StoredMemory

MINUTES_PER_DAY = 24 * 60
PAGE = 100


def namespace(tenant_id: uuid.UUID, customer_id: str) -> tuple[str, str]:
    """Memory lives under (tenant, customer). Store labels may not contain periods."""
    return str(tenant_id), quote(customer_id, safe="").replace(".", "%2E")


class MemoryStore:
    """Long-term customer memory on the LangGraph store, always scoped by tenant and customer."""

    def __init__(self, backend: AsyncPostgresStore) -> None:
        self.backend = backend
        self.ttl_minutes = get_settings().memory_ttl_days * MINUTES_PER_DAY
        self.max_records = get_settings().memory_max_records

    async def list(
        self, tenant_id: uuid.UUID, customer_id: str, limit: int | None = None
    ) -> list[StoredMemory]:
        items = await self.backend.asearch(
            namespace(tenant_id, customer_id), limit=self.max_records + PAGE
        )
        items.sort(key=lambda item: item.updated_at, reverse=True)
        memories = [
            StoredMemory(
                key=item.key,
                category=MemoryCategory(item.value["category"]),
                text=item.value["text"],
                conversation_id=item.value.get("conversation_id"),
                updated_at=item.updated_at,
            )
            for item in items
        ]
        return memories[:limit] if limit else memories

    async def save(
        self,
        tenant_id: uuid.UUID,
        customer_id: str,
        records: Sequence[MemoryRecord],
        conversation_id: uuid.UUID | None = None,
    ) -> int:
        """Write records (a repeated key replaces the older fact) and enforce the size cap."""
        ns = namespace(tenant_id, customer_id)
        for record in records:
            value: dict[str, Any] = {
                "category": record.category.value,
                "text": record.text,
                "conversation_id": str(conversation_id) if conversation_id else None,
            }
            await self.backend.aput(ns, record.key, value, index=False, ttl=self.ttl_minutes)
        await self._trim(tenant_id, customer_id)
        return len(records)

    async def _trim(self, tenant_id: uuid.UUID, customer_id: str) -> None:
        stored = await self.list(tenant_id, customer_id)
        ns = namespace(tenant_id, customer_id)
        for stale in stored[self.max_records :]:
            await self.backend.adelete(ns, stale.key)

    async def forget(self, tenant_id: uuid.UUID, customer_id: str) -> int:
        """Delete every memory of one customer, including expired rows not swept yet."""
        ns = namespace(tenant_id, customer_id)
        removed = 0
        while items := await self.backend.asearch(ns, limit=PAGE):
            for item in items:
                await self.backend.adelete(ns, item.key)
            removed += len(items)
        await self.backend.sweep_ttl()
        return removed

    async def sweep(self) -> int:
        return await self.backend.sweep_ttl()


async def setup_store_tables() -> None:
    """Create the store tables as the owner so the app role inherits access."""
    dsn = psycopg_dsn(get_settings().admin_database_url)
    async with AsyncPostgresStore.from_conn_string(dsn) as backend:
        await backend.setup()


@asynccontextmanager
async def open_memory_store() -> AsyncIterator[MemoryStore]:
    """A store backed by a small pool that connects as the restricted app role."""
    pool = AsyncConnectionPool(
        conninfo=psycopg_dsn(get_settings().database_url),
        max_size=5,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    try:
        ttl = TTLConfig(refresh_on_read=False, omit_expired=True)
        yield MemoryStore(AsyncPostgresStore(pool, ttl=ttl))  # type: ignore[arg-type]
    finally:
        await pool.close()


def main() -> None:
    use_selector_loop_on_windows()
    asyncio.run(setup_store_tables())
    print("memory store tables are ready")


if __name__ == "__main__":
    main()
