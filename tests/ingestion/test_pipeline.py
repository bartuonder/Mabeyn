import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from mabeyn.db import models as m
from mabeyn.db.session import tenant_session
from mabeyn.ingestion import pipeline
from mabeyn.ingestion.pipeline import add_document, ingest_document
from tests.fakes import FakeLLM

MARKDOWN = b"""---
language: en
effective_date: 2025-03-01
---
# Return Policy

## Eligibility

Items can be returned within thirty days of delivery with the original receipt.

## Refunds

Refunds are issued to the original payment method within five business days.
"""


async def create(tenant_id: uuid.UUID, data: bytes | None, **kwargs) -> uuid.UUID:
    async with tenant_session(tenant_id) as s:
        document = await add_document(
            s,
            tenant_id=tenant_id,
            title=kwargs.pop("title", "Return Policy"),
            source_type=kwargs.pop("source_type", m.DocumentSource.MARKDOWN),
            data=data,
            **kwargs,
        )
        return document.id


async def load(tenant_id: uuid.UUID, document_id: uuid.UUID):
    async with tenant_session(tenant_id) as s:
        document = await s.get(m.Document, document_id)
        chunks = (
            await s.scalars(
                select(m.Chunk).where(m.Chunk.document_id == document_id).order_by(m.Chunk.position)
            )
        ).all()
    return document, chunks


async def test_ingestion_creates_embedded_chunks(fresh_tenant):
    document_id = await create(fresh_tenant, MARKDOWN)
    status = await ingest_document(fresh_tenant, document_id, llm=FakeLLM())
    document, chunks = await load(fresh_tenant, document_id)

    assert status is m.DocumentStatus.READY
    assert document.status is m.DocumentStatus.READY and document.error is None
    assert document.language == "en"
    assert document.effective_date == date(2025, 3, 1)
    assert chunks and all(c.embedding is not None and len(c.embedding) == 1536 for c in chunks)
    assert all(c.tenant_id == fresh_tenant for c in chunks)
    assert chunks[0].context_header.startswith("Return Policy")
    assert "Eligibility" in chunks[0].section


async def test_reingestion_replaces_chunks(fresh_tenant):
    document_id = await create(fresh_tenant, MARKDOWN)
    await ingest_document(fresh_tenant, document_id, llm=FakeLLM())
    _, first = await load(fresh_tenant, document_id)
    await ingest_document(fresh_tenant, document_id, llm=FakeLLM())
    _, second = await load(fresh_tenant, document_id)
    assert len(first) == len(second)
    assert {c.id for c in first}.isdisjoint({c.id for c in second})


async def test_unparseable_document_is_marked_failed(fresh_tenant):
    document_id = await create(fresh_tenant, b"---\ntitle: nothing\n---\n")
    status = await ingest_document(fresh_tenant, document_id, llm=FakeLLM())
    document, chunks = await load(fresh_tenant, document_id)
    assert status is m.DocumentStatus.FAILED
    assert document.status is m.DocumentStatus.FAILED
    assert "no text" in (document.error or "")
    assert chunks == []


async def test_unexpected_errors_do_not_leak_details(fresh_tenant):
    class BrokenLLM(FakeLLM):
        async def embed(self, texts, *, model=None):
            raise RuntimeError("secret internal detail")

    document_id = await create(fresh_tenant, MARKDOWN)
    status = await ingest_document(fresh_tenant, document_id, llm=BrokenLLM())
    document, _ = await load(fresh_tenant, document_id)
    assert status is m.DocumentStatus.FAILED
    assert "secret" not in (document.error or "")


async def test_url_documents_are_fetched_then_parsed(fresh_tenant, monkeypatch):
    async def fake_fetch(url: str):
        return b"<h1>Shipping</h1><p>We ship in two days.</p>", m.DocumentSource.HTML

    monkeypatch.setattr(pipeline, "fetch_url", fake_fetch)
    document_id = await create(
        fresh_tenant,
        None,
        title="Shipping page",
        source_type=m.DocumentSource.URL,
        source_uri="https://shop.example.com/shipping",
    )
    status = await ingest_document(fresh_tenant, document_id, llm=FakeLLM())
    _, chunks = await load(fresh_tenant, document_id)
    assert status is m.DocumentStatus.READY
    assert "two days" in chunks[0].content


async def test_chunks_are_invisible_to_other_tenants(fresh_tenant, tenant_a):
    document_id = await create(fresh_tenant, MARKDOWN)
    await ingest_document(fresh_tenant, document_id, llm=FakeLLM())
    async with tenant_session(tenant_a.id) as s:
        count = await s.scalar(
            select(func.count()).select_from(m.Chunk).where(m.Chunk.document_id == document_id)
        )
    assert count == 0


async def test_unknown_document_raises(fresh_tenant):
    with pytest.raises(LookupError):
        await ingest_document(fresh_tenant, uuid.uuid4(), llm=FakeLLM())
