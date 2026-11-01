import uuid
from typing import Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from mabeyn.core.config import get_settings

INGEST_JOB = "ingest"


class IngestionQueue(Protocol):
    async def enqueue(self, tenant_id: uuid.UUID, document_id: uuid.UUID) -> None: ...

    async def close(self) -> None: ...


class ArqQueue:
    def __init__(self) -> None:
        self._pool: ArqRedis | None = None

    async def enqueue(self, tenant_id: uuid.UUID, document_id: uuid.UUID) -> None:
        if self._pool is None:
            self._pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
        await self._pool.enqueue_job(INGEST_JOB, str(tenant_id), str(document_id))

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None


_queue = ArqQueue()


def get_ingestion_queue() -> IngestionQueue:
    return _queue
