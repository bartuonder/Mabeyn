import uuid
from collections.abc import Sequence
from dataclasses import replace

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mabeyn.db.models import Chunk, Document, DocumentStatus
from mabeyn.rag.retrieval import to_retrieved, vector_search
from mabeyn.rag.types import RetrievedChunk

RRF_K = 60

FTS_SQL = text(
    """
    WITH q AS (
        SELECT
            to_tsquery(
                'turkish', replace(plainto_tsquery('turkish', :query)::text, '&', '|')
            ) AS tr,
            to_tsquery(
                'english', replace(plainto_tsquery('english', :query)::text, '&', '|')
            ) AS en
    )
    SELECT c.id,
           ts_rank_cd(
               c.search_vector,
               CASE c.fts_config WHEN 'turkish'::regconfig THEN q.tr ELSE q.en END
           ) AS rank
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    CROSS JOIN q
    WHERE c.tenant_id = :tenant_id
      AND d.status = :ready
      AND (
          (c.fts_config = 'turkish'::regconfig AND c.search_vector @@ q.tr)
          OR (c.fts_config = 'english'::regconfig AND c.search_vector @@ q.en)
      )
    ORDER BY rank DESC
    LIMIT :limit
    """
)


async def fts_search(
    session: AsyncSession, tenant_id: uuid.UUID, query: str, k: int
) -> list[RetrievedChunk]:
    rows = (
        await session.execute(
            FTS_SQL,
            {
                "query": query,
                "tenant_id": tenant_id,
                "ready": DocumentStatus.READY.value,
                "limit": k,
            },
        )
    ).all()
    if not rows:
        return []
    ranks = {row.id: float(row.rank) for row in rows}
    return await _load(session, tenant_id, ranks)


async def _load(
    session: AsyncSession, tenant_id: uuid.UUID, scores: dict[uuid.UUID, float]
) -> list[RetrievedChunk]:
    statement = (
        select(Chunk, Document.title, Document.source_uri, Document.effective_date)
        .join(Document, Chunk.document_id == Document.id)
        .where(Chunk.tenant_id == tenant_id, Chunk.id.in_(list(scores)))
    )
    loaded = [
        to_retrieved(row, scores[row[0].id]) for row in (await session.execute(statement)).all()
    ]
    return sorted(loaded, key=lambda c: c.score, reverse=True)


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[RetrievedChunk]], k: int = RRF_K
) -> list[RetrievedChunk]:
    """Merge ranked lists: each appearance adds 1 / (k + rank) to the chunk's score."""
    scores: dict[uuid.UUID, float] = {}
    chunks: dict[uuid.UUID, RetrievedChunk] = {}
    for ranking in rankings:
        for position, chunk in enumerate(ranking, start=1):
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (k + position)
            chunks.setdefault(chunk.chunk_id, chunk)
    fused = [replace(chunks[cid], score=score) for cid, score in scores.items()]
    return sorted(fused, key=lambda c: c.score, reverse=True)


async def hybrid_search(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    query: str,
    query_vector: Sequence[float],
    k: int,
    pool: int = 30,
) -> list[RetrievedChunk]:
    vector_hits = await vector_search(session, tenant_id, query_vector, pool)
    text_hits = await fts_search(session, tenant_id, query, pool)
    return reciprocal_rank_fusion([vector_hits, text_hits])[:k]
