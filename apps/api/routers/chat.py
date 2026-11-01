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
from apps.api.schemas import ChatRequest
from mabeyn.db.models import Conversation, Message, MessageRole, Tenant
from mabeyn.db.session import tenant_session
from mabeyn.llm.client import ChatMessage
from mabeyn.rag.service import Final, RagService, Retract, Token, get_rag_service

router = APIRouter(prefix="/chat", tags=["chat"])
log = structlog.get_logger()

Rag = Annotated[RagService, Depends(get_rag_service)]


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


async def _open_conversation(
    tenant_id: uuid.UUID, customer_id: str, body: ChatRequest
) -> tuple[uuid.UUID, str, list[ChatMessage]]:
    async with tenant_session(tenant_id) as session:
        company = await session.scalar(select(Tenant.name)) or "the company"
        history: list[ChatMessage] = []
        if body.conversation_id is None:
            conversation = Conversation(tenant_id=tenant_id, customer_id=customer_id)
            session.add(conversation)
            await session.flush()
        else:
            existing = await session.get(Conversation, body.conversation_id)
            if existing is None or existing.customer_id != customer_id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
            conversation = existing
            rows = await session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .where(Message.role.in_([MessageRole.USER, MessageRole.ASSISTANT]))
                .order_by(Message.created_at.desc())
                .limit(10)
            )
            history = [{"role": m.role.value, "content": m.content} for m in reversed(rows.all())]
        session.add(
            Message(
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content=body.message,
            )
        )
        return conversation.id, company, history


async def _save_answer(tenant_id: uuid.UUID, conversation_id: uuid.UUID, final: Final) -> uuid.UUID:
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


@router.post("")
async def chat(body: ChatRequest, customer: Customer, rag: Rag) -> StreamingResponse:
    conversation_id, company, history = await _open_conversation(
        customer.tenant_id, customer.customer_id, body
    )

    async def events() -> AsyncIterator[str]:
        yield sse("meta", {"conversation_id": str(conversation_id)})
        try:
            final: Final | None = None
            async for event in rag.run(customer.tenant_id, body.message, history, company):
                if isinstance(event, Token):
                    yield sse("token", {"text": event.text})
                elif isinstance(event, Retract):
                    yield sse("retract", {})
                else:
                    final = event
            if final is None:
                raise RuntimeError("RAG service produced no answer")
            message_id = await _save_answer(customer.tenant_id, conversation_id, final)
            citations = [
                {k: v for k, v in asdict(c).items() if k != "chunk_ids"} for c in final.citations
            ]
            yield sse("citations", citations)
            yield sse("done", {"message_id": str(message_id), "answer": final.answer})
        except Exception:
            log.exception("chat_failed", conversation_id=str(conversation_id))
            yield sse("error", {"message": "The assistant could not complete this request."})

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )
