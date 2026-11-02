import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from pydantic import ValidationError
from sqlalchemy import select

from mabeyn.core.config import get_settings
from mabeyn.db import models as m
from mabeyn.db.session import tenant_session
from mabeyn.guard.basic import check_input
from mabeyn.llm.client import LLM, LLMError, chat_json
from mabeyn.memory import prompts
from mabeyn.memory.hygiene import screen
from mabeyn.memory.schema import MemoryRecord, StoredMemory
from mabeyn.memory.store import MemoryStore

log = structlog.get_logger()

MAX_MESSAGES = 20
MAX_MESSAGE_CHARS = 500
MAX_CANDIDATES = 12
LOGGED_REASONS = frozenset({"instruction", "markup"})


@dataclass(frozen=True)
class Extraction:
    saved: int
    rejected: int


def _known(known: Sequence[StoredMemory]) -> str:
    return "\n".join(f"- {k.key}: {k.text}" for k in known) or "(none)"


async def extract_candidates(
    llm: LLM, messages: Sequence[str], known: Sequence[StoredMemory]
) -> list[MemoryRecord]:
    """Ask the small model for facts; anything that does not fit the schema is dropped."""
    numbered = "\n".join(f"{i}. {text}" for i, text in enumerate(messages, start=1))
    try:
        data = await chat_json(
            llm,
            [
                {"role": "system", "content": prompts.EXTRACT_SYSTEM},
                {
                    "role": "user",
                    "content": prompts.EXTRACT_USER.format(known=_known(known), messages=numbered),
                },
            ],
            model=get_settings().llm_model_small,
            max_tokens=600,
        )
        items = data["memories"]
    except (LLMError, KeyError, TypeError):
        return []
    records: dict[str, MemoryRecord] = {}
    for item in items[:MAX_CANDIDATES] if isinstance(items, list) else []:
        try:
            record = MemoryRecord.model_validate(item)
        except ValidationError:
            continue
        records[record.key] = record
    return list(records.values())


async def _customer_messages(
    tenant_id: uuid.UUID, conversation_id: uuid.UUID
) -> tuple[str, list[str]] | None:
    async with tenant_session(tenant_id) as session:
        conversation = await session.get(m.Conversation, conversation_id)
        if conversation is None:
            return None
        rows = await session.scalars(
            select(m.Message.content)
            .where(m.Message.conversation_id == conversation_id)
            .where(m.Message.role == m.MessageRole.USER)
            .order_by(m.Message.created_at.desc())
            .limit(MAX_MESSAGES)
        )
        texts = [check_input(text).text[:MAX_MESSAGE_CHARS] for text in reversed(rows.all())]
        return conversation.customer_id, [t for t in texts if t]


async def _log_rejections(
    tenant_id: uuid.UUID, conversation_id: uuid.UUID, rejected: list[tuple[MemoryRecord, str, str]]
) -> None:
    if not rejected:
        return
    async with tenant_session(tenant_id) as session:
        for record, reason, detail in rejected:
            event_detail = {
                "reason": reason,
                "category": record.category.value,
                "slug": record.slug,
            }
            if reason in LOGGED_REASONS:
                event_detail["text"] = record.text[:160]
                event_detail["rule"] = detail[:80]
            session.add(
                m.SecurityEvent(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    layer="memory",
                    category=f"memory_{reason}",
                    action=m.SecurityAction.BLOCKED,
                    detail=event_detail,
                )
            )


async def extract_conversation(
    llm: LLM, store: MemoryStore, tenant_id: uuid.UUID, conversation_id: uuid.UUID
) -> Extraction:
    """Turn what the customer said in one conversation into stored memories."""
    loaded = await _customer_messages(tenant_id, conversation_id)
    if loaded is None:
        return Extraction(0, 0)
    customer_id, messages = loaded
    accepted: list[MemoryRecord] = []
    rejected: list[tuple[MemoryRecord, str, str]] = []
    if messages:
        known = await store.list(tenant_id, customer_id)
        for record in await extract_candidates(llm, messages, known):
            rejection = screen(record)
            if rejection is None:
                accepted.append(record)
            else:
                rejected.append((record, rejection.reason, rejection.detail))
    await store.save(tenant_id, customer_id, accepted, conversation_id)
    await _log_rejections(tenant_id, conversation_id, rejected)
    async with tenant_session(tenant_id) as session:
        conversation = await session.get(m.Conversation, conversation_id)
        if conversation is not None:
            conversation.memory_extracted_at = datetime.now(UTC)
    log.info("memory_extracted", saved=len(accepted), rejected=len(rejected))
    return Extraction(len(accepted), len(rejected))
