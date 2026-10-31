import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from apps.api.deps import TenantDb
from apps.api.schemas import DocumentOut
from mabeyn.db.models import Document

router = APIRouter(prefix="/documents", tags=["documents"])


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
