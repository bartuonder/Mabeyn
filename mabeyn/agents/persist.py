import uuid

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
