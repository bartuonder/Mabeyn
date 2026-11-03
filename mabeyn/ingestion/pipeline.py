import asyncio
import uuid
from datetime import date

import structlog
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from mabeyn.db.models import Chunk, Document, DocumentSource, DocumentStatus, SecurityAction
from mabeyn.db.session import tenant_session
from mabeyn.guard.classifier import Verdict
from mabeyn.guard.events import record_event, snippet
from mabeyn.guard.firewall import Firewall
from mabeyn.ingestion.chunk import ChunkConfig, DraftChunk, chunk_document
from mabeyn.ingestion.fetch import FetchError, fetch_url
from mabeyn.ingestion.parse import ParseError, detect_language, parse_document
from mabeyn.llm.client import LLM, get_llm

log = structlog.get_logger()

EXPECTED_ERRORS = (ParseError, FetchError)
FTS_CONFIGS = {"tr": "turkish", "en": "english"}
SCAN_CONCURRENCY = 8


async def add_document(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    title: str,
    source_type: DocumentSource,
    data: bytes | None = None,
    source_uri: str | None = None,
    language: str | None = None,
    effective_date: date | None = None,
) -> Document:
    document = Document(
        tenant_id=tenant_id,
        title=title,
        source_type=source_type,
        source_uri=source_uri,
        raw_content=data,
        size_bytes=len(data) if data is not None else None,
        effective_date=effective_date,
        **({"language": language} if language else {}),
    )
    session.add(document)
    await session.flush()
    return document


async def _set_failed(tenant_id: uuid.UUID, document_id: uuid.UUID, message: str) -> None:
    async with tenant_session(tenant_id) as session:
        document = await session.get(Document, document_id)
        if document is not None:
            document.status = DocumentStatus.FAILED
            document.error = message[:500]


async def _scan(
    firewall: Firewall | None, tenant_id: uuid.UUID, chunks: list[DraftChunk]
) -> tuple[list[Verdict | None], float]:
    """Rate every passage for instructions aimed at an assistant (indirect injection)."""
    if firewall is None or not firewall.config.documents:
        return [None] * len(chunks), 1.0
    settings = await firewall.settings(tenant_id)
    if not settings.firewall_enabled:
        return [None] * len(chunks), 1.0
    gate = asyncio.Semaphore(SCAN_CONCURRENCY)

    async def one(chunk: DraftChunk) -> Verdict:
        async with gate:
            return await firewall.scan_chunk(chunk.content)

    return list(await asyncio.gather(*(one(c) for c in chunks))), settings.injection_threshold


async def ingest_document(
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    llm: LLM | None = None,
    chunk_config: ChunkConfig | None = None,
    embedding_model: str | None = None,
    firewall: Firewall | None = None,
) -> DocumentStatus:
    """Parse, chunk and embed one document. Failures are stored on the document."""
    llm = llm or get_llm()

    async with tenant_session(tenant_id) as session:
        document = await session.get(Document, document_id, options=[undefer(Document.raw_content)])
        if document is None:
            raise LookupError(f"document {document_id} not found")
        document.status = DocumentStatus.PROCESSING
        document.error = None
        title, source_type, source_uri = document.title, document.source_type, document.source_uri
        data = document.raw_content
        preset_date = document.effective_date

    try:
        if source_type is DocumentSource.URL:
            if not source_uri:
                raise FetchError("URL document has no source URI")
            data, fetched_type = await fetch_url(source_uri)
            source_type = fetched_type
        if data is None:
            raise ParseError("document has no content")

        parsed = parse_document(source_type, data)
        chunks = chunk_document(parsed, title, chunk_config)
        vectors = await llm.embed([c.embedding_text for c in chunks], model=embedding_model)
        verdicts, threshold = await _scan(firewall, tenant_id, chunks)
    except EXPECTED_ERRORS as exc:
        await _set_failed(tenant_id, document_id, str(exc))
        return DocumentStatus.FAILED
    except Exception:
        log.exception("ingestion_failed", document_id=str(document_id))
        await _set_failed(tenant_id, document_id, "unexpected error during ingestion")
        return DocumentStatus.FAILED

    language = parsed.metadata.get("language") or detect_language(parsed.text)
    effective = preset_date
    if raw_date := parsed.metadata.get("effective_date"):
        try:
            effective = date.fromisoformat(raw_date)
        except ValueError:
            effective = preset_date

    async with tenant_session(tenant_id) as session:
        document = await session.get(Document, document_id)
        if document is None:
            return DocumentStatus.FAILED
        await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
        session.add_all(
            Chunk(
                tenant_id=tenant_id,
                document_id=document_id,
                position=chunk.position,
                content=chunk.content,
                context_header=chunk.context_header,
                section=chunk.section,
                page=chunk.page,
                token_count=chunk.token_count,
                embedding=vector,
                fts_config=FTS_CONFIGS.get(language, "simple"),
                flagged=bool(verdict and verdict.score >= threshold),
                injection_score=verdict.score if verdict else None,
            )
            for chunk, vector, verdict in zip(chunks, vectors, verdicts, strict=True)
        )
        document.language = language
        document.effective_date = effective
        document.status = DocumentStatus.READY
    for chunk, verdict in zip(chunks, verdicts, strict=True):
        if verdict and verdict.score >= threshold:
            await record_event(
                tenant_id,
                None,
                "ingestion",
                verdict.category,
                SecurityAction.FLAGGED,
                {
                    "document_id": str(document_id),
                    "position": chunk.position,
                    "score": verdict.score,
                    "text": snippet(chunk.content),
                },
            )
    log.info("ingestion_done", document_id=str(document_id), chunks=len(chunks))
    return DocumentStatus.READY
