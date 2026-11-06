import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from sqlalchemy.engine import make_url

from mabeyn.core.config import get_settings
from mabeyn.core.loop import use_selector_loop_on_windows
from mabeyn.db.tenant_context import TenantPool, checkpoint_policies


def psycopg_dsn(sqlalchemy_url: str) -> str:
    """Turn the SQLAlchemy URL used elsewhere into a libpq connection string."""
    return make_url(sqlalchemy_url).set(drivername="postgresql").render_as_string(False)


async def setup_checkpoint_tables() -> None:
    """Create the checkpoint tables. Runs as the owner so the app role inherits access."""
    dsn = psycopg_dsn(get_settings().admin_database_url)
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        await saver.setup()
        for statement in checkpoint_policies():
            await saver.conn.execute(statement)  # type: ignore[union-attr]


@asynccontextmanager
async def open_checkpointer() -> AsyncIterator[AsyncPostgresSaver]:
    """A saver backed by a small pool that connects as the restricted app role.

    Rows are only visible inside a `tenant_scope` of the owning tenant (row-level security).
    """
    pool = TenantPool(
        conninfo=psycopg_dsn(get_settings().database_url),
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    try:
        yield AsyncPostgresSaver(pool)  # type: ignore[arg-type]
    finally:
        await pool.close()


def main() -> None:
    use_selector_loop_on_windows()
    asyncio.run(setup_checkpoint_tables())
    print("checkpoint tables are ready")


if __name__ == "__main__":
    main()
