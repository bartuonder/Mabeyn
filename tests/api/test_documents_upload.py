import io
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from apps.api.main import app
from apps.api.routers import documents as documents_router
from mabeyn.core.config import get_settings
from mabeyn.db.models import Document
from mabeyn.db.session import tenant_session
from mabeyn.ingestion.fetch import FetchError
from mabeyn.queue import get_ingestion_queue
from tests.api.test_api_keys import auth_headers
from tests.factories import SeededTenant
from tests.fakes import FakeQueue


@pytest.fixture
def queue():
    fake = FakeQueue()
    app.dependency_overrides[get_ingestion_queue] = lambda: fake
    yield fake
    app.dependency_overrides.clear()


def upload(client: AsyncClient, headers, name: str, content: bytes, **data):
    return client.post(
        "/documents", headers=headers, files={"file": (name, io.BytesIO(content))}, data=data
    )


async def test_markdown_upload_is_stored_and_queued(client, tenant_x: SeededTenant, queue):
    headers = await auth_headers(client, tenant_x)
    content = b"# Returns\n\nThirty days."
    response = await upload(client, headers, "returns.md", content, effective_date="2025-01-01")
    assert response.status_code == 202
    body = response.json()
    document_id = uuid.UUID(body["id"])
    assert body["status"] == "pending" and body["title"] == "returns"
    assert body["effective_date"] == "2025-01-01"
    assert queue.jobs == [(tenant_x.id, document_id)]
    assert "raw_content" not in body

    async with tenant_session(tenant_x.id) as s:
        stored = await s.get(Document, document_id)
        assert stored is not None and stored.size_bytes == len(content)


async def test_custom_title_and_path_traversal_in_filename(client, tenant_x, queue):
    headers = await auth_headers(client, tenant_x)
    response = await upload(client, headers, "../../etc/notes.md", b"# x\n\ntext", title="My Title")
    assert response.status_code == 202
    assert response.json()["title"] == "My Title"


@pytest.mark.parametrize("name", ["malware.exe", "notes.txt", "noextension"])
async def test_unsupported_extensions_are_rejected(client, tenant_x, queue, name):
    headers = await auth_headers(client, tenant_x)
    assert (await upload(client, headers, name, b"data")).status_code == 415
    assert queue.jobs == []


async def test_fake_pdf_and_binary_text_are_rejected(client, tenant_x, queue):
    headers = await auth_headers(client, tenant_x)
    assert (await upload(client, headers, "a.pdf", b"not a pdf")).status_code == 422
    assert (await upload(client, headers, "a.md", b"\xff\xfe\x00\x01")).status_code == 422
    assert (await upload(client, headers, "a.md", b"   ")).status_code == 422
    assert queue.jobs == []


async def test_oversized_upload_is_rejected(client, tenant_x, queue, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "50")
    get_settings.cache_clear()
    try:
        headers = await auth_headers(client, tenant_x)
        response = await upload(client, headers, "big.md", b"x" * 100)
    finally:
        monkeypatch.delenv("MAX_UPLOAD_BYTES")
        get_settings.cache_clear()
    assert response.status_code == 413


async def test_queue_failure_marks_document_failed(client, tenant_x):
    app.dependency_overrides[get_ingestion_queue] = lambda: FakeQueue(fail=True)
    try:
        headers = await auth_headers(client, tenant_x)
        response = await upload(client, headers, "q.md", b"# t\n\ntext")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    async with tenant_session(tenant_x.id) as s:
        failed = (
            await s.scalars(select(Document).where(Document.error == "could not queue ingestion"))
        ).first()
    assert failed is not None


async def test_url_document_is_queued(client, tenant_x, queue, monkeypatch):
    async def allow(url: str, resolver) -> None:
        return None

    monkeypatch.setattr(documents_router, "ensure_public", allow)
    headers = await auth_headers(client, tenant_x)
    response = await client.post(
        "/documents/url", headers=headers, json={"url": "https://shop.example.com/faq"}
    )
    assert response.status_code == 202
    assert response.json()["title"] == "https://shop.example.com/faq"
    assert len(queue.jobs) == 1


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/admin", "http://169.254.169.254/latest", "file:///etc/passwd"]
)
async def test_private_and_non_http_urls_are_rejected(client, tenant_x, queue, url):
    headers = await auth_headers(client, tenant_x)
    response = await client.post("/documents/url", headers=headers, json={"url": url})
    assert response.status_code == 422
    assert queue.jobs == []


async def test_uploads_require_authentication(client, queue):
    assert (await upload(client, {}, "a.md", b"# x\n\ny")).status_code == 401
    response = await client.post("/documents/url", json={"url": "https://example.com"})
    assert response.status_code == 401


async def test_delete_is_scoped_to_tenant(client, tenant_x, tenant_y, queue):
    a_headers = await auth_headers(client, tenant_x)
    b_headers = await auth_headers(client, tenant_y)
    created = (await upload(client, a_headers, "d.md", b"# t\n\ntext")).json()
    path = f"/documents/{created['id']}"

    assert (await client.delete(path, headers=b_headers)).status_code == 404
    assert (await client.delete(path, headers=a_headers)).status_code == 204
    assert (await client.get(path, headers=a_headers)).status_code == 404


async def test_fetch_error_message_is_returned(client, tenant_x, queue, monkeypatch):
    async def reject(url: str, resolver) -> None:
        raise FetchError("URL resolves to a non-public address")

    monkeypatch.setattr(documents_router, "ensure_public", reject)
    headers = await auth_headers(client, tenant_x)
    response = await client.post(
        "/documents/url", headers=headers, json={"url": "https://x.example"}
    )
    assert "non-public" in response.json()["detail"]
