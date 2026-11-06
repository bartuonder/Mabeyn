import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

current_tenant: ContextVar[uuid.UUID | None] = ContextVar("current_tenant", default=None)

# LangGraph tables that are not managed by SQLAlchemy. Their rows carry the tenant in a column:
# checkpoints use the thread id "<tenant>:<conversation>", the memory store a dotted namespace
# that starts with the tenant.
CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
STORE_TABLES = ("store",)
_SETTING = "NULLIF(current_setting('app.tenant_id', true), '')"


@contextmanager
def tenant_scope(tenant_id: uuid.UUID) -> Iterator[None]:
    """Everything the pooled LangGraph connections do inside this block runs as this tenant."""
    token = current_tenant.set(tenant_id)
    try:
        yield
    finally:
        try:
            current_tenant.reset(token)
        except ValueError:
            # An async generator closed from another context cannot restore the old value.
            current_tenant.set(None)


class TenantPool(AsyncConnectionPool):
    """A pool that tells PostgreSQL which tenant a borrowed connection works for.

    Outside a `tenant_scope` the setting is empty and the row-level policies show no rows.
    """

    @asynccontextmanager
    async def connection(
        self,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> AsyncIterator[AsyncConnection[Any]]:
        async with super().connection(timeout) as conn:
            tenant = current_tenant.get()
            await conn.execute(
                "SELECT set_config('app.tenant_id', %s, false)", (str(tenant) if tenant else "",)
            )
            try:
                yield conn
            finally:
                await conn.execute("SELECT set_config('app.tenant_id', '', false)")


def policy_statements(tables: tuple[str, ...], expression: str) -> list[str]:
    statements: list[str] = []
    for table in tables:
        statements += [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS tenant_isolation ON {table}",
            f"CREATE POLICY tenant_isolation ON {table} USING ({expression})",
        ]
    return statements


def checkpoint_policies() -> list[str]:
    return policy_statements(CHECKPOINT_TABLES, f"split_part(thread_id, ':', 1) = {_SETTING}")


def store_policies() -> list[str]:
    return policy_statements(STORE_TABLES, f"split_part(prefix, '.', 1) = {_SETTING}")
