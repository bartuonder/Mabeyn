import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from apps.api.deps import TenantDb
from apps.api.schemas import ConversationDetail, ConversationOut, MessageOut
from mabeyn.db.models import Conversation, Message

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("")
async def list_conversations(db: TenantDb) -> list[ConversationOut]:
    rows = await db.scalars(select(Conversation).order_by(Conversation.created_at.desc()))
    return [ConversationOut.model_validate(row) for row in rows]


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: uuid.UUID, db: TenantDb) -> ConversationDetail:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    messages = await db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    return ConversationDetail(
        **ConversationOut.model_validate(conversation).model_dump(),
        messages=[MessageOut.model_validate(m) for m in messages],
    )
