import uuid
from contextlib import AsyncExitStack
from typing import Any

from arq import cron
from arq.connections import RedisSettings

from mabeyn.core.config import get_settings
from mabeyn.core.logging import configure_logging
from mabeyn.core.tracking import export_tracking_env
from mabeyn.guard.classifier import LlmInjectionClassifier
from mabeyn.guard.firewall import Firewall
from mabeyn.ingestion.pipeline import ingest_document
from mabeyn.llm.client import get_llm
from mabeyn.memory.idle import extract_idle
from mabeyn.memory.service import MemoryService
from mabeyn.memory.store import open_memory_store


async def ingest(ctx: dict[str, Any], tenant_id: str, document_id: str) -> str:
    status = await ingest_document(
        uuid.UUID(tenant_id), uuid.UUID(document_id), firewall=ctx.get("firewall")
    )
    return status.value


async def mine_idle_conversations(ctx: dict[str, Any]) -> int:
    return await extract_idle(ctx["memory"])


async def sweep_expired_memories(ctx: dict[str, Any]) -> int:
    swept: int = await ctx["memory"].store.sweep()
    return swept


async def startup(ctx: dict[str, Any]) -> None:
    export_tracking_env()
    configure_logging(get_settings().log_level)
    stack = AsyncExitStack()
    store = await stack.enter_async_context(open_memory_store())
    ctx["stack"] = stack
    llm = get_llm()
    ctx["memory"] = MemoryService(llm, store)
    ctx["firewall"] = Firewall(LlmInjectionClassifier(llm))


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["stack"].aclose()


class WorkerSettings:
    functions = [ingest]
    cron_jobs = [
        cron(mine_idle_conversations, minute=set(range(0, 60, 5)), run_at_startup=False),
        cron(sweep_expired_memories, minute={17}, run_at_startup=False),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    job_timeout = 600
    max_tries = 1
