import json

import httpx
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from apps.kargoshop_site.main import Settings, create_app


class FakeBackend:
    """Stands in for the Mabeyn API and the mock shop; records what the site sent."""

    def __init__(self, login_ok: bool = True) -> None:
        self.login_ok = login_ok
        self.sessions: list[tuple[str, dict]] = []
        self.created: list[dict] = []
        self.revoked: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/auth/login":
            if not self.login_ok:
                return httpx.Response(401, json={"detail": "no"})
            return httpx.Response(200, json={"access_token": "admin-token"})
        if path == "/api-keys" and request.method == "GET":
            old = {"id": "old", "name": "kargoshop-site-secret", "revoked_at": None}
            return httpx.Response(200, json=[old])
        if path == "/api-keys" and request.method == "POST":
            body = json.loads(request.content)
            self.created.append(body)
            return httpx.Response(201, json={"key": f"key-{body['kind']}"})
        if path.startswith("/api-keys/") and request.method == "DELETE":
            self.revoked.append(path.rsplit("/", 1)[1])
            return httpx.Response(204)
        if path == "/sessions":
            self.sessions.append((request.headers["x-api-key"], json.loads(request.content)))
            return httpx.Response(200, json={"token": "customer-token", "expires_in": 900})
        if path == "/orders":
            return httpx.Response(200, json=[{"id": "KS-1"}])
        if path == "/refunds":
            return httpx.Response(200, json=[])
        return httpx.Response(404)


@pytest_asyncio.fixture
async def backend() -> FakeBackend:
    return FakeBackend()


@pytest_asyncio.fixture
async def site(backend: FakeBackend):
    app = create_app(Settings(), transport=httpx.MockTransport(backend))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://site") as client:
        yield client


async def test_visitors_get_the_publishable_key_and_older_keys_are_revoked(site, backend):
    me = (await site.get("/me")).json()
    assert me["customer"] is None and me["publishable_key"] == "key-publishable"
    assert backend.revoked == ["old"]
    kinds = {c["kind"]: c["allowed_origins"] for c in backend.created}
    assert kinds == {"secret": [], "publishable": ["http://localhost:9100"]}


async def test_a_signed_in_customer_gets_a_session_opened_with_the_secret_key(site, backend):
    assert (await site.post("/login", json={"customer": "customer-2"})).status_code == 200
    me = (await site.get("/me")).json()
    assert me["customer"] == "customer-2" and me["name"] == "Mehmet"

    session = await site.post("/support/session")
    assert session.json()["token"] == "customer-token"
    assert backend.sessions == [("key-secret", {"customer_id": "customer-2"})]


async def test_a_session_needs_a_signed_in_customer(site, backend):
    assert (await site.post("/support/session")).status_code == 401
    assert backend.sessions == []


async def test_an_unknown_customer_cannot_sign_in(site):
    await site.post("/login", json={"customer": "customer-99"})
    assert (await site.get("/me")).json()["customer"] is None


async def test_the_site_says_when_the_support_service_is_not_ready():
    backend = FakeBackend(login_ok=False)
    app = create_app(Settings(), transport=httpx.MockTransport(backend))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://site") as client:
        await client.post("/login", json={"customer": "customer-1"})
        assert (await client.post("/support/session")).status_code == 503
        assert (await client.get("/me")).json()["publishable_key"] is None


async def test_the_storefront_page_points_the_widget_at_the_api(site):
    page = (await site.get("/")).text
    assert "{{API_URL}}" not in page and "http://localhost:8000" in page
    orders = (await site.get("/api/orders")).json()
    assert orders == {"orders": [], "refunds": []}
