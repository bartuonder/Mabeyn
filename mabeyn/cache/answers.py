import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Literal, Protocol

import structlog
from redis.asyncio import Redis
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from mabeyn.core.config import get_settings
from mabeyn.core.text import fold
from mabeyn.db.models import AnswerCacheEntry
from mabeyn.db.session import tenant_session
from mabeyn.guard.pii import mask_pii
from mabeyn.llm.client import LLM
from mabeyn.prompt_registry import current

log = structlog.get_logger()

MAX_QUESTION_CHARS = 300
REFRESHED = ("answer", "citations", "embedding", "expires_at")
IDENTIFIER = re.compile(r"[A-Za-z]{1,4}-?\d{3,}")
PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)


class KeyValueStore(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...

    async def incr(self, key: str) -> int: ...


class MemoryKV:
    """In-process store for tests."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self.data[key] = value

    async def incr(self, key: str) -> int:
        self.data[key] = str(int(self.data.get(key, "0")) + 1)
        return int(self.data[key])


class RedisKV:
    def __init__(self, client: Redis) -> None:
        self.client = client

    async def get(self, key: str) -> str | None:
        value = await self.client.get(key)
        return value.decode() if isinstance(value, bytes) else value

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await self.client.set(key, value, ex=ttl_seconds)

    async def incr(self, key: str) -> int:
        return int(await self.client.incr(key))


@lru_cache
def get_kv() -> KeyValueStore:
    return RedisKV(Redis.from_url(get_settings().redis_url))


@dataclass(frozen=True)
class CachedAnswer:
    answer: str
    citations: list[dict[str, Any]]
    similarity: float
    layer: Literal["exact", "semantic"]


def normalise(question: str) -> str:
    return " ".join(PUNCTUATION.sub(" ", fold(question)).split())


def cacheable_question(question: str) -> bool:
    """Questions with personal data or order numbers are about one customer and never cached."""
    if not question or len(question) > MAX_QUESTION_CHARS:
        return False
    return not IDENTIFIER.search(question) and not mask_pii(question).changed


def cacheable_answer(answer: str) -> bool:
    return bool(answer.strip()) and not mask_pii(answer).changed


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@lru_cache
def prompt_hash() -> str:
    """A change to any prompt makes every older entry unreachable."""
    return _digest(json.dumps(sorted(current().items())))


class AnswerCache:
    """Exact-match entries in Redis, then a semantic search in pgvector, always per tenant."""

    def __init__(self, llm: LLM, kv: KeyValueStore) -> None:
        self.llm = llm
        self.kv = kv

    async def _exact_key(self, tenant_id: uuid.UUID, question: str) -> str:
        epoch = await self.kv.get(f"cache:epoch:{tenant_id}") or "0"
        return f"cache:{tenant_id}:{epoch}:{prompt_hash()[:12]}:{_digest(normalise(question))[:32]}"

    async def lookup(self, tenant_id: uuid.UUID, question: str) -> CachedAnswer | None:
        if not cacheable_question(question):
            return None
        raw = await self.kv.get(await self._exact_key(tenant_id, question))
        if raw is not None:
            data = json.loads(raw)
            return CachedAnswer(data["answer"], data["citations"], 1.0, "exact")
        return await self._semantic(tenant_id, question)

    async def _semantic(self, tenant_id: uuid.UUID, question: str) -> CachedAnswer | None:
        vector = (await self.llm.embed([normalise(question)]))[0]
        distance = AnswerCacheEntry.embedding.cosine_distance(vector)
        async with tenant_session(tenant_id) as session:
            row = (
                await session.execute(
                    select(AnswerCacheEntry, distance)
                    .where(
                        AnswerCacheEntry.prompt_hash == prompt_hash(),
                        AnswerCacheEntry.expires_at > datetime.now(UTC),
                    )
                    .order_by(distance)
                    .limit(1)
                )
            ).first()
            if row is None:
                return None
            entry, dist = row
            similarity = 1.0 - float(dist)
            if similarity < get_settings().cache_semantic_threshold:
                return None
            await session.execute(
                update(AnswerCacheEntry)
                .where(AnswerCacheEntry.id == entry.id)
                .values(hits=entry.hits + 1)
            )
            return CachedAnswer(entry.answer, entry.citations, similarity, "semantic")

    async def store(
        self, tenant_id: uuid.UUID, question: str, answer: str, citations: list[dict[str, Any]]
    ) -> bool:
        if not (cacheable_question(question) and cacheable_answer(answer)):
            return False
        settings = get_settings()
        ttl = settings.cache_ttl_seconds
        payload = json.dumps({"answer": answer, "citations": citations}, ensure_ascii=False)
        await self.kv.set(await self._exact_key(tenant_id, question), payload, ttl)

        vector = (await self.llm.embed([normalise(question)]))[0]
        values = {
            "tenant_id": tenant_id,
            "question_hash": _digest(normalise(question)),
            "prompt_hash": prompt_hash(),
            "question": question,
            "answer": answer,
            "citations": citations,
            "embedding": vector,
            "expires_at": datetime.now(UTC) + timedelta(seconds=ttl),
        }
        async with tenant_session(tenant_id) as session:
            statement = insert(AnswerCacheEntry).values(**values)
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["tenant_id", "prompt_hash", "question_hash"],
                    set_={
                        k: statement.excluded[k]
                        for k in ("answer", "citations", "embedding", "expires_at")
                    },
                )
            )
            await self._trim(session)
        return True

    async def _trim(self, session: Any) -> None:
        keep = (
            select(AnswerCacheEntry.id)
            .order_by(AnswerCacheEntry.created_at.desc())
            .limit(get_settings().cache_max_entries)
        )
        await session.execute(delete(AnswerCacheEntry).where(AnswerCacheEntry.id.not_in(keep)))

    async def invalidate(self, tenant_id: uuid.UUID) -> None:
        await invalidate_answers(tenant_id, self.kv)


async def invalidate_answers(tenant_id: uuid.UUID, kv: KeyValueStore | None = None) -> None:
    """Drop every cached answer of a tenant, for example after its documents changed."""
    try:
        await (kv or get_kv()).incr(f"cache:epoch:{tenant_id}")
        async with tenant_session(tenant_id) as session:
            await session.execute(delete(AnswerCacheEntry))
    except Exception:
        log.exception("cache_invalidation_failed", tenant_id=str(tenant_id))
