import uuid
from typing import Any

from arq.connections import RedisSettings

from mabeyn.core.config import get_settings
from mabeyn.core.logging import configure_logging
from mabeyn.core.tracking import export_tracking_env
from mabeyn.ingestion.pipeline import ingest_document


async def ingest(ctx: dict[str, Any], tenant_id: str, document_id: str) -> str:
    status = await ingest_document(uuid.UUID(tenant_id), uuid.UUID(document_id))
    return status.value


async def startup(ctx: dict[str, Any]) -> None:
    export_tracking_env()
    configure_logging(get_settings().log_level)


class WorkerSettings:
    functions = [ingest]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    job_timeout = 600
    max_tries = 1
