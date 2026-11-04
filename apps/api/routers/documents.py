import uuid
from datetime import date
from pathlib import PurePath
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import select

from apps.api.deps import Admin, TenantDb
from apps.api.schemas import DocumentOut, UrlDocumentCreate
from mabeyn.cache.answers import invalidate_answers
from mabeyn.core.config import get_settings
from mabeyn.db.models import Document, DocumentSource, DocumentStatus
from mabeyn.db.session import tenant_session
from mabeyn.ingestion.fetch import FetchError, ensure_public, resolve_host
from mabeyn.ingestion.parse import ParseError, decode_text
from mabeyn.ingestion.pipeline import add_document
from mabeyn.queue import IngestionQueue, get_ingestion_queue

router = APIRouter(prefix="/documents", tags=["documents"])

EXTENSIONS = {
    ".pdf": DocumentSource.PDF,
    ".md": DocumentSource.MARKDOWN,
    ".markdown": DocumentSource.MARKDOWN,
    ".html": DocumentSource.HTML,
    ".htm": DocumentSource.HTML,
}

Queue = Annotated[IngestionQueue, Depends(get_ingestion_queue)]


async def _read_limited(file: UploadFile) -> bytes:
    limit = get_settings().max_upload_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "File is too large")
    if not data.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "File is empty")
    return data


def _validate_content(source_type: DocumentSource, data: bytes) -> None:
    if source_type is DocumentSource.PDF:
        if not data.startswith(b"%PDF-"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "File is not a valid PDF")
        return
    try:
        decode_text(data)
    except ParseError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "File must be UTF-8 text"
        ) from None


async def _create_and_enqueue(admin: Admin, queue: IngestionQueue, **fields: object) -> DocumentOut:
    async with tenant_session(admin.tenant_id) as session:
        document = await add_document(session, tenant_id=admin.tenant_id, **fields)  # type: ignore[arg-type]
        out = DocumentOut.model_validate(document)
    # Enqueue after commit so the worker always sees the row.
    try:
        await queue.enqueue(admin.tenant_id, out.id)
    except Exception:
        async with tenant_session(admin.tenant_id) as session:
            failed = await session.get(Document, out.id)
            if failed is not None:
                failed.status = DocumentStatus.FAILED
                failed.error = "could not queue ingestion"
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Queue unavailable") from None
    return out


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    admin: Admin,
    queue: Queue,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form(max_length=500)] = None,
    effective_date: Annotated[date | None, Form()] = None,
) -> DocumentOut:
    filename = PurePath(file.filename or "").name
    source_type = EXTENSIONS.get(PurePath(filename).suffix.lower())
    if source_type is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Supported types: PDF, Markdown, HTML"
        )
    data = await _read_limited(file)
    _validate_content(source_type, data)
    return await _create_and_enqueue(
        admin,
        queue,
        title=(title or PurePath(filename).stem)[:500],
        source_type=source_type,
        data=data,
        source_uri=filename,
        effective_date=effective_date,
    )


@router.post("/url", status_code=status.HTTP_202_ACCEPTED)
async def add_url_document(body: UrlDocumentCreate, admin: Admin, queue: Queue) -> DocumentOut:
    try:
        await ensure_public(body.url, resolve_host)
    except FetchError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return await _create_and_enqueue(
        admin,
        queue,
        title=(body.title or body.url)[:500],
        source_type=DocumentSource.URL,
        source_uri=body.url,
        effective_date=body.effective_date,
    )


@router.get("")
async def list_documents(db: TenantDb) -> list[DocumentOut]:
    rows = await db.scalars(select(Document).order_by(Document.created_at))
    return [DocumentOut.model_validate(row) for row in rows]


@router.get("/{document_id}")
async def get_document(document_id: uuid.UUID, db: TenantDb) -> DocumentOut:
    document = await db.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return DocumentOut.model_validate(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: uuid.UUID, admin: Admin, db: TenantDb) -> Response:
    document = await db.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    await db.delete(document)
    await db.commit()
    # Answers built from the removed document must not be served any longer.
    await invalidate_answers(admin.tenant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
