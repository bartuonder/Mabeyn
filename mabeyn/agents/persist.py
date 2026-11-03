import uuid

from sqlalchemy import select

from mabeyn.db.models import Message, MessageRole
from mabeyn.db.session import tenant_session
from mabeyn.rag.service import Final


async def save_assistant_message(
    tenant_id: uuid.UUID, conversation_id: uuid.UUID, final: Final
) -> uuid.UUID:
    citations = [
        {
            "cite": c.cite,
            "document_id": str(c.document_id),
            "document": c.document_title,
            "section": c.section,
            "page": c.page,
        }
        for c in final.citations
    ]
    async with tenant_session(tenant_id) as session:
        message = Message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content=final.answer,
            citations=citations,
        )
        session.add(message)
        await session.flush()
        return message.id


async def mark_blocked(tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
    """Keep a blocked customer message for review but out of memory and summaries."""
    async with tenant_session(tenant_id) as session:
        latest = await session.scalar(
            select(Message)
            .where(Message.conversation_id == conversation_id, Message.role == MessageRole.USER)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        if latest is not None:
            latest.blocked = True
