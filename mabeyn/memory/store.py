import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote

from langgraph.store.base import PutOp, SearchItem, SearchOp, TTLConfig
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg.rows import dict_row
from sqlalchemy.engine import make_url

from mabeyn.agents.checkpoint import psycopg_dsn
from mabeyn.core.config import get_settings
from mabeyn.core.loop import use_selector_loop_on_windows
from mabeyn.db.tenant_context import TenantPool, store_policies, tenant_scope
from mabeyn.memory.schema import MemoryCategory, MemoryRecord, StoredMemory

MINUTES_PER_DAY = 24 * 60
# Row-level security hides every tenant but one, so the cross-tenant TTL sweep is a function.
SWEEP_FUNCTION = (
    """
    CREATE OR REPLACE FUNCTION sweep_expired_store() RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public
    AS $$
    DECLARE removed integer;
    BEGIN
        DELETE FROM store WHERE expires_at IS NOT NULL AND expires_at < now();
        GET DIAGNOSTICS removed = ROW_COUNT;
        RETURN removed;
    END
    $$
    """,
    "REVOKE ALL ON FUNCTION sweep_expired_store() FROM PUBLIC",
)
PAGE = 100


def namespace(tenant_id: uuid.UUID, customer_id: str) -> tuple[str, str]:
    """Memory lives under (tenant, customer). Store labels may not contain periods."""
    return str(tenant_id), quote(customer_id, safe="").replace(".", "%2E")


class MemoryStore:
    """Long-term customer memory on the LangGraph store, always scoped by tenant and customer."""

    def __init__(self, backend: AsyncPostgresStore, pool: TenantPool) -> None:
        self.backend = backend
        self.pool = pool
        self.ttl_minutes = get_settings().memory_ttl_days * MINUTES_PER_DAY
        self.max_records = get_settings().memory_max_records

    # The store's own aput/asearch hand work to a background task, which would not see the tenant
    # of the caller. `abatch` runs the operations in the calling task instead.
    async def _search(self, ns: tuple[str, ...], limit: int) -> list[SearchItem]:
        (items,) = await self.backend.abatch(
            [SearchOp(ns, None, limit, 0, None, refresh_ttl=False)]
        )
        return list(items)  # type: ignore[arg-type]

    async def _put(self, ns: tuple[str, ...], key: str, value: dict[str, Any] | None) -> None:
        ttl = self.ttl_minutes if value is not None else None
        await self.backend.abatch([PutOp(ns, key, value, False, ttl=ttl)])

    async def list(
        self, tenant_id: uuid.UUID, customer_id: str, limit: int | None = None
    ) -> list[StoredMemory]:
        with tenant_scope(tenant_id):
            items = await self._search(namespace(tenant_id, customer_id), self.max_records + PAGE)
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
        with tenant_scope(tenant_id):
            for record in records:
                value: dict[str, Any] = {
                    "category": record.category.value,
                    "text": record.text,
                    "conversation_id": str(conversation_id) if conversation_id else None,
                }
                await self._put(ns, record.key, value)
            await self._trim(tenant_id, customer_id)
        return len(records)

    async def _trim(self, tenant_id: uuid.UUID, customer_id: str) -> None:
        stored = await self.list(tenant_id, customer_id)
        ns = namespace(tenant_id, customer_id)
        with tenant_scope(tenant_id):
            for stale in stored[self.max_records :]:
                await self._put(ns, stale.key, None)

    async def forget(self, tenant_id: uuid.UUID, customer_id: str) -> int:
        """Delete every memory of one customer, including expired rows not swept yet."""
        ns = namespace(tenant_id, customer_id)
        removed = 0
        with tenant_scope(tenant_id):
            while items := await self._search(ns, PAGE):
                for item in items:
                    await self._put(ns, item.key, None)
                removed += len(items)
            await self.backend.sweep_ttl()
        return removed

    async def sweep(self) -> int:
        """Delete expired rows of every tenant through a narrow function (no tenant context)."""
        async with self.pool.connection() as conn:
            cursor = await conn.execute("SELECT sweep_expired_store()")
            row = await cursor.fetchone()
        return int(row["sweep_expired_store"]) if row else 0


async def setup_store_tables() -> None:
    """Create the store tables as the owner so the app role inherits access."""
    dsn = psycopg_dsn(get_settings().admin_database_url)
    role = make_url(get_settings().database_url).username or ""
    async with AsyncPostgresStore.from_conn_string(dsn) as backend:
        await backend.setup()
        for statement in [*store_policies(), *SWEEP_FUNCTION]:
            await backend.conn.execute(statement)  # type: ignore[union-attr]
        quoted = '"' + role.replace('"', '""') + '"'
        await backend.conn.execute(  # type: ignore[union-attr]
            f"GRANT EXECUTE ON FUNCTION sweep_expired_store() TO {quoted}"
        )


@asynccontextmanager
async def open_memory_store() -> AsyncIterator[MemoryStore]:
    """A store backed by a small pool that connects as the restricted app role."""
    pool = TenantPool(
        conninfo=psycopg_dsn(get_settings().database_url),
        max_size=5,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    try:
        ttl = TTLConfig(refresh_on_read=False, omit_expired=True)
        yield MemoryStore(AsyncPostgresStore(pool, ttl=ttl), pool)  # type: ignore[arg-type]
    finally:
        await pool.close()


def main() -> None:
    use_selector_loop_on_windows()
    asyncio.run(setup_store_tables())
    print("memory store tables are ready")


if __name__ == "__main__":
    main()
