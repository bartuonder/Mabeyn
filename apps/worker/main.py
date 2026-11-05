import uuid
from typing import Any

from arq import cron
from arq.connections import RedisSettings

from mabeyn.agents.approvals import expire_stale_approvals
from mabeyn.agents.runtime import AgentRuntime
from mabeyn.core.config import get_settings
from mabeyn.core.logging import configure_logging
from mabeyn.core.tracking import export_tracking_env
from mabeyn.guard.classifier import LlmInjectionClassifier
from mabeyn.guard.firewall import Firewall
from mabeyn.ingestion.pipeline import ingest_document
from mabeyn.llm.client import get_llm
from mabeyn.memory.idle import extract_idle


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


async def expire_pending_approvals(ctx: dict[str, Any]) -> int:
    return await expire_stale_approvals(ctx["runtime"].service)


async def startup(ctx: dict[str, Any]) -> None:
    export_tracking_env()
    configure_logging(get_settings().log_level)
    runtime = AgentRuntime()
    await runtime.start()
    ctx["runtime"] = runtime
    ctx["memory"] = runtime.memory
    ctx["firewall"] = Firewall(LlmInjectionClassifier(get_llm()))


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["runtime"].stop()


class WorkerSettings:
    functions = [ingest]
    cron_jobs = [
        cron(mine_idle_conversations, minute=set(range(0, 60, 5)), run_at_startup=False),
        cron(sweep_expired_memories, minute={17}, run_at_startup=False),
        cron(expire_pending_approvals, minute=set(range(3, 60, 10)), run_at_startup=False),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    job_timeout = 600
    max_tries = 1
