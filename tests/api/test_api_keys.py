from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabeyn.auth import tokens
from tests.factories import SeededTenant


async def auth_headers(client: AsyncClient, tenant: SeededTenant) -> dict[str, str]:
    response = await client.post(
        "/auth/login", json={"email": tenant.admin_email, "password": tenant.admin_password}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def create_key(client: AsyncClient, headers: dict[str, str], name: str = "widget") -> dict:
    response = await client.post("/api-keys", json={"name": name}, headers=headers)
    assert response.status_code == 201
    return response.json()


async def test_endpoints_require_authentication(client: AsyncClient):
    assert (await client.get("/api-keys")).status_code == 401
    assert (await client.post("/api-keys", json={"name": "x"})).status_code == 401
    bad = {"Authorization": "Bearer not-a-token"}
    assert (await client.get("/api-keys", headers=bad)).status_code == 401


async def test_key_is_shown_once_and_stored_hashed(
    client: AsyncClient, tenant_a: SeededTenant, admin_engine: AsyncEngine
):
    headers = await auth_headers(client, tenant_a)
    created = await create_key(client, headers)
    assert created["key"].startswith("mbn_")

    listed = (await client.get("/api-keys", headers=headers)).json()
    entry = next(k for k in listed if k["id"] == created["id"])
    assert "key" not in entry
    assert entry["prefix"] == created["key"][: len(entry["prefix"])]

    async with admin_engine.connect() as conn:
        stored = (
            await conn.execute(
                text("SELECT key_hash FROM api_keys WHERE id = :id"), {"id": created["id"]}
            )
        ).scalar_one()
    assert stored != created["key"]
    assert created["key"] not in stored


async def test_customer_session_carries_tenant_and_customer(
    client: AsyncClient, tenant_a: SeededTenant
):
    created = await create_key(client, await auth_headers(client, tenant_a))
    response = await client.post(
        "/sessions", json={"customer_id": "cust-42"}, headers={"X-API-Key": created["key"]}
    )
    assert response.status_code == 200
    claims = tokens.decode_customer_token(response.json()["token"])
    assert claims.tenant_id == tenant_a.id
    assert claims.customer_id == "cust-42"
    assert response.json()["expires_in"] == 15 * 60


async def test_session_rejects_missing_or_invalid_key(client: AsyncClient):
    body = {"customer_id": "cust-1"}
    assert (await client.post("/sessions", json=body)).status_code == 401
    assert (
        await client.post("/sessions", json=body, headers={"X-API-Key": "mbn_x"})
    ).status_code == 401


async def test_revoked_key_cannot_open_sessions(client: AsyncClient, tenant_a: SeededTenant):
    headers = await auth_headers(client, tenant_a)
    created = await create_key(client, headers)
    key_header = {"X-API-Key": created["key"]}
    body = {"customer_id": "cust-1"}
    assert (await client.post("/sessions", json=body, headers=key_header)).status_code == 200

    assert (await client.delete(f"/api-keys/{created['id']}", headers=headers)).status_code == 204
    assert (await client.post("/sessions", json=body, headers=key_header)).status_code == 401


async def test_tenant_cannot_see_or_revoke_other_tenants_keys(
    client: AsyncClient, tenant_a: SeededTenant, tenant_b: SeededTenant
):
    a_headers = await auth_headers(client, tenant_a)
    b_headers = await auth_headers(client, tenant_b)
    key_a = await create_key(client, a_headers, name="a-key")

    b_listing = (await client.get("/api-keys", headers=b_headers)).json()
    assert key_a["id"] not in {k["id"] for k in b_listing}
    assert (await client.delete(f"/api-keys/{key_a['id']}", headers=b_headers)).status_code == 404

    a_listing = (await client.get("/api-keys", headers=a_headers)).json()
    entry = next(k for k in a_listing if k["id"] == key_a["id"])
    assert entry["revoked_at"] is None
