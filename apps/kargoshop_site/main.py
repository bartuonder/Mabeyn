"""The demo tenant's storefront: a small site that embeds the Mabeyn widget.

It plays the part of the tenant's own server: it knows who is signed in and opens the customer's
chat session with a secret API key, so the browser never holds that key.
"""

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).resolve().parent / "static"
CUSTOMERS = {"customer-1": "Ayşe", "customer-2": "Mehmet", "customer-3": "Elif"}
COOKIE = "shop_customer"
KEY_PREFIX = "kargoshop-site"


class Settings:
    def __init__(self) -> None:
        env = os.environ.get
        self.api_url = env("MABEYN_API_URL", "http://localhost:8000").rstrip("/")
        self.public_api_url = env("MABEYN_PUBLIC_API_URL", "http://localhost:8000").rstrip("/")
        self.shop_url = env("SHOP_API_URL", "http://localhost:9000").rstrip("/")
        self.site_origin = env("SITE_ORIGIN", "http://localhost:9100")
        self.admin_email = env("DEMO_ADMIN_EMAIL", "admin@kargoshop.example")
        self.admin_password = env("DEMO_ADMIN_PASSWORD", "demo-admin-password")


class Keys:
    """Creates the site's two API keys on first use and revokes older ones."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport
        self.secret: str | None = None
        self.publishable: str | None = None
        self._lock = asyncio.Lock()

    async def ready(self) -> bool:
        async with self._lock:
            if self.secret and self.publishable:
                return True
            s = self.settings
            async with httpx.AsyncClient(base_url=s.api_url, transport=self.transport) as client:
                login = await client.post(
                    "/auth/login", json={"email": s.admin_email, "password": s.admin_password}
                )
                if login.status_code != 200:
                    return False
                headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
                for key in (await client.get("/api-keys", headers=headers)).json():
                    if key["name"].startswith(KEY_PREFIX) and key["revoked_at"] is None:
                        await client.delete(f"/api-keys/{key['id']}", headers=headers)
                self.secret = await self._create(client, headers, "secret", [])
                self.publishable = await self._create(
                    client, headers, "publishable", [s.site_origin]
                )
            return True

    @staticmethod
    async def _create(
        client: httpx.AsyncClient, headers: dict[str, str], kind: str, origins: list[str]
    ) -> str:
        response = await client.post(
            "/api-keys",
            headers=headers,
            json={"name": f"{KEY_PREFIX}-{kind}", "kind": kind, "allowed_origins": origins},
        )
        response.raise_for_status()
        return str(response.json()["key"])


def create_app(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    settings = settings or Settings()
    keys = Keys(settings, transport)
    app = FastAPI(title="KargoShop storefront", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def home() -> str:
        page = (STATIC / "index.html").read_text(encoding="utf-8")
        return page.replace("{{API_URL}}", settings.public_api_url)

    @app.get("/me")
    async def me(request: Request) -> dict[str, Any]:
        """Who is signed in, and the publishable key anonymous visitors use in the browser."""
        customer = request.cookies.get(COOKIE)
        ready = await keys.ready()
        return {
            "customer": customer if customer in CUSTOMERS else None,
            "name": CUSTOMERS.get(customer or ""),
            "customers": CUSTOMERS,
            "publishable_key": keys.publishable if ready else None,
        }

    @app.post("/login")
    async def login(body: dict[str, str | None]) -> Response:
        customer = body.get("customer")
        response = JSONResponse({"customer": customer if customer in CUSTOMERS else None})
        if customer in CUSTOMERS:
            response.set_cookie(COOKIE, customer, samesite="lax")
        else:
            response.delete_cookie(COOKIE)
        return response

    @app.get("/api/orders")
    async def orders(request: Request) -> JSONResponse:
        customer = request.cookies.get(COOKIE)
        if customer not in CUSTOMERS:
            return JSONResponse({"orders": [], "refunds": []})
        async with httpx.AsyncClient(base_url=settings.shop_url, transport=transport) as client:
            found = (await client.get("/orders", params={"customer_id": customer})).json()
            refunds = (await client.get("/refunds", params={"customer_id": customer})).json()
        return JSONResponse({"orders": found, "refunds": refunds})

    @app.post("/support/session")
    async def support_session(request: Request) -> JSONResponse:
        """Open a chat session for the signed-in customer with the secret key."""
        customer = request.cookies.get(COOKIE)
        if customer not in CUSTOMERS:
            return JSONResponse({"detail": "Sign in first"}, status_code=401)
        if not await keys.ready():
            return JSONResponse({"detail": "The support service is not ready"}, status_code=503)
        async with httpx.AsyncClient(base_url=settings.api_url, transport=transport) as client:
            response = await client.post(
                "/sessions",
                headers={"X-API-Key": keys.secret or ""},
                json={"customer_id": customer},
            )
        return JSONResponse(response.json(), status_code=response.status_code)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
