import uuid

import structlog

from mabeyn.memory.hygiene import screen_text
from mabeyn.memory.store import MemoryStore

log = structlog.get_logger()


async def recall(
    store: MemoryStore, tenant_id: uuid.UUID, customer_id: str, limit: int
) -> list[dict[str, str]]:
    """Notes about this customer, newest first. Stored text is screened again before use."""
    notes = []
    for memory in await store.list(tenant_id, customer_id, limit):
        if screen_text(memory.text) is not None:
            log.warning("memory_skipped_on_recall", key=memory.key)
            continue
        notes.append({"category": memory.category.value, "text": memory.text})
    return notes
