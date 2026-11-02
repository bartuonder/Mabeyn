import uuid

import structlog
from sqlalchemy import text

from mabeyn.core.config import get_settings
from mabeyn.db.session import anonymous_session
from mabeyn.memory.service import MemoryService

log = structlog.get_logger()

BATCH = 50


async def idle_conversations(
    idle_minutes: int | None = None, limit: int = BATCH
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Conversations whose last message is old and that were not mined for memories yet."""
    minutes = idle_minutes if idle_minutes is not None else get_settings().memory_idle_minutes
    async with anonymous_session() as session:
        rows = await session.execute(
            text("SELECT tenant_id, conversation_id FROM memory_idle_conversations(:m, :n)"),
            {"m": minutes, "n": limit},
        )
        return [(row.tenant_id, row.conversation_id) for row in rows]


async def extract_idle(service: MemoryService, limit: int = BATCH) -> int:
    """Mine every idle conversation once. A failure leaves the conversation for the next run."""
    done = 0
    for tenant_id, conversation_id in await idle_conversations(limit=limit):
        try:
            await service.extract(tenant_id, conversation_id)
            done += 1
        except Exception:
            log.exception("idle_extraction_failed", conversation_id=str(conversation_id))
    return done
