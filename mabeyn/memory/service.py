import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import update

from mabeyn.db import models as m
from mabeyn.db.session import tenant_session
from mabeyn.llm.client import LLM, ChatMessage
from mabeyn.memory.extract import Extraction, extract_conversation
from mabeyn.memory.schema import StoredMemory
from mabeyn.memory.short_term import Compacted, compact
from mabeyn.memory.store import MemoryStore


class MemoryService:
    """The one entry point the API and the worker use for memory."""

    def __init__(self, llm: LLM, store: MemoryStore) -> None:
        self.llm = llm
        self.store = store

    async def compact(
        self, summary: str, summarized: int, pending: Sequence[ChatMessage]
    ) -> Compacted:
        return await compact(self.llm, summary, summarized, pending)

    async def extract(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> Extraction:
        return await extract_conversation(self.llm, self.store, tenant_id, conversation_id)

    async def list(self, tenant_id: uuid.UUID, customer_id: str) -> list[StoredMemory]:
        return await self.store.list(tenant_id, customer_id)

    async def forget(self, tenant_id: uuid.UUID, customer_id: str) -> int:
        """Right to be forgotten: drop notes and summaries and stop old chats being mined again."""
        async with tenant_session(tenant_id) as session:
            conversations = m.Conversation.customer_id == customer_id
            await session.execute(
                update(m.Conversation)
                .where(conversations, m.Conversation.memory_extracted_at.is_(None))
                .values(memory_extracted_at=datetime.now(UTC))
            )
            await session.execute(
                update(m.Conversation)
                .where(conversations)
                .values(summary=None, summarized_messages=0)
            )
        return await self.store.forget(tenant_id, customer_id)
