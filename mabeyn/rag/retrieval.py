import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mabeyn.db.models import Chunk, Document, DocumentStatus
from mabeyn.db.session import tenant_session
from mabeyn.llm.client import LLM
from mabeyn.rag.types import RetrievedChunk


def to_retrieved(row: Any, score: float) -> RetrievedChunk:
    chunk, title, source_uri, effective_date = row[0], row[1], row[2], row[3]
    return RetrievedChunk(
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        document_title=title,
        source_uri=source_uri,
        section=chunk.section or "",
        page=chunk.page,
        content=chunk.content,
        context_header=chunk.context_header,
        effective_date=effective_date,
        score=score,
    )


async def vector_search(
    session: AsyncSession, tenant_id: uuid.UUID, query_vector: Sequence[float], k: int
) -> list[RetrievedChunk]:
    # Iterative scans keep the tenant filter from starving the approximate index.
    await session.execute(text("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true)"))
    await session.execute(text("SELECT set_config('hnsw.ef_search', '100', true)"))
    distance = Chunk.embedding.cosine_distance(list(query_vector))
    statement = (
        select(Chunk, Document.title, Document.source_uri, Document.effective_date, distance)
        .join(Document, Chunk.document_id == Document.id)
        .where(
            Chunk.tenant_id == tenant_id,
            Chunk.embedding.is_not(None),
            Chunk.flagged.is_(False),
            Document.status == DocumentStatus.READY,
        )
        .order_by(distance)
        .limit(k)
    )
    rows = (await session.execute(statement)).all()
    return [to_retrieved(row, 1.0 - float(row[4])) for row in rows]


async def embed_query(llm: LLM, query: str, model: str | None = None) -> list[float]:
    return (await llm.embed([query], model=model))[0]


async def retrieve_vector(
    tenant_id: uuid.UUID, llm: LLM, query: str, k: int = 5, embedding_model: str | None = None
) -> list[RetrievedChunk]:
    vector = await embed_query(llm, query, embedding_model)
    async with tenant_session(tenant_id) as session:
        return await vector_search(session, tenant_id, vector, k)
