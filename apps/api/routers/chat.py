import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from apps.api.deps import Customer
from apps.api.schemas import (
    ChatRequest,
    ConversationClosed,
    ConversationDetail,
    ConversationOut,
    MessageOut,
)
from mabeyn.agents.persist import save_assistant_message
from mabeyn.agents.runtime import get_agent_service, get_memory_service
from mabeyn.agents.service import AgentService, TurnContext
from mabeyn.db.models import Conversation, ConversationStatus, Message, MessageRole, Tenant
from mabeyn.db.session import tenant_session
from mabeyn.llm.client import ChatMessage
from mabeyn.memory.service import MemoryService
from mabeyn.memory.short_term import Compacted
from mabeyn.rag.service import Final, Retract, Token

router = APIRouter(prefix="/chat", tags=["chat"])
log = structlog.get_logger()

Agents = Annotated[AgentService, Depends(get_agent_service)]
Memories = Annotated[MemoryService, Depends(get_memory_service)]


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


async def _open_conversation(
    memory: MemoryService, tenant_id: uuid.UUID, customer_id: str, body: ChatRequest
) -> tuple[TurnContext, list[ChatMessage], bool]:
    summary, summarized = "", 0
    pending: list[ChatMessage] = []
    async with tenant_session(tenant_id) as session:
        company = await session.scalar(select(Tenant.name)) or "the company"
        if body.conversation_id is None:
            conversation = Conversation(tenant_id=tenant_id, customer_id=customer_id)
            session.add(conversation)
            await session.flush()
        else:
            existing = await session.get(Conversation, body.conversation_id)
            if existing is None or existing.customer_id != customer_id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
            conversation = existing
            summary, summarized = conversation.summary or "", conversation.summarized_messages
            rows = await session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .where(Message.role.in_([MessageRole.USER, MessageRole.ASSISTANT]))
                .order_by(Message.created_at)
                .offset(summarized)
            )
            pending = [{"role": m.role.value, "content": m.content} for m in rows]
        conversation_id = conversation.id
        escalated = conversation.status is ConversationStatus.ESCALATED

    # A human owns escalated conversations, so no model call is spent on their context.
    compacted = (
        Compacted(summary, summarized, [])
        if escalated
        else await memory.compact(summary, summarized, pending)
    )
    async with tenant_session(tenant_id) as session:
        current = await session.get(Conversation, conversation_id)
        if current is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
        if compacted.summarized != summarized:
            current.summary = compacted.summary
            current.summarized_messages = compacted.summarized
        if current.status is ConversationStatus.CLOSED:
            current.status = ConversationStatus.OPEN
        # New messages may hold new facts, so the conversation is mined again when it goes idle.
        current.memory_extracted_at = None
        session.add(
            Message(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                role=MessageRole.USER,
                content=body.message,
            )
        )
    turn = TurnContext(tenant_id, customer_id, conversation_id, company, summary=compacted.summary)
    return turn, compacted.history, escalated


@router.post("")
async def chat(
    body: ChatRequest, customer: Customer, agents: Agents, memory: Memories
) -> StreamingResponse:
    turn, history, escalated = await _open_conversation(
        memory, customer.tenant_id, customer.customer_id, body
    )

    async def events() -> AsyncIterator[str]:
        yield sse("meta", {"conversation_id": str(turn.conversation_id)})
        if escalated:
            # A human colleague owns this conversation; the message is stored for them.
            yield sse("escalated", {})
            yield sse("done", {"message_id": None, "answer": ""})
            return
        try:
            final: Final | None = None
            async for event in agents.run(turn, body.message, history):
                if isinstance(event, Token):
                    yield sse("token", {"text": event.text})
                elif isinstance(event, Retract):
                    yield sse("retract", {})
                elif isinstance(event, Final):
                    final = event
            if final is None:
                raise RuntimeError("the agent produced no answer")
            message_id = await save_assistant_message(turn.tenant_id, turn.conversation_id, final)
            citations = [
                {k: v for k, v in asdict(c).items() if k != "chunk_ids"} for c in final.citations
            ]
            yield sse("citations", citations)
            if final.status == "pending_approval":
                yield sse("approval", {"approval_id": str(final.approval_id)})
            elif final.status == "escalated":
                yield sse("escalated", {})
            yield sse("done", {"message_id": str(message_id), "answer": final.answer})
        except Exception:
            log.exception("chat_failed", conversation_id=str(turn.conversation_id))
            yield sse("error", {"message": "The assistant could not complete this request."})

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.post("/{conversation_id}/close")
async def close_conversation(
    conversation_id: uuid.UUID, customer: Customer, memory: Memories
) -> ConversationClosed:
    """End a conversation and keep what the customer said that is worth remembering."""
    async with tenant_session(customer.tenant_id) as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None or conversation.customer_id != customer.customer_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
        if conversation.status is ConversationStatus.OPEN:
            conversation.status = ConversationStatus.CLOSED
        state = conversation.status
    saved = blocked = 0
    try:
        result = await memory.extract(customer.tenant_id, conversation_id)
        saved, blocked = result.saved, result.rejected
    except Exception:
        # The conversation stays unmarked, so the idle job tries again.
        log.exception("memory_extraction_failed", conversation_id=str(conversation_id))
    return ConversationClosed(status=state, memories_saved=saved, memories_blocked=blocked)


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: uuid.UUID, customer: Customer) -> ConversationDetail:
    """Lets the widget fetch answers that arrive later, for example after an approval."""
    async with tenant_session(customer.tenant_id) as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None or conversation.customer_id != customer.customer_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
        rows = await session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .where(Message.role.in_([MessageRole.USER, MessageRole.ASSISTANT]))
            .order_by(Message.created_at)
        )
        return ConversationDetail(
            **ConversationOut.model_validate(conversation).model_dump(),
            messages=[MessageOut.model_validate(m) for m in rows],
        )
