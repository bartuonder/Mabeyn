from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabeyn.auth import tokens
from tests.factories import SeededTenant, delete_tenant, seed_tenant


async def login(client: AsyncClient, tenant: SeededTenant, password: str | None = None):
    return await client.post(
        "/auth/login",
        json={"email": tenant.admin_email, "password": password or tenant.admin_password},
    )


async def test_login_returns_scoped_tokens(client: AsyncClient, tenant_a: SeededTenant):
    response = await login(client, tenant_a)
    assert response.status_code == 200
    body = response.json()
    claims = tokens.decode_access_token(body["access_token"])
    assert claims.tenant_id == tenant_a.id
    assert claims.admin_id == tenant_a.admin_id
    assert body["token_type"] == "bearer"


async def test_login_email_is_case_insensitive(client: AsyncClient, tenant_a: SeededTenant):
    response = await client.post(
        "/auth/login",
        json={"email": tenant_a.admin_email.upper(), "password": tenant_a.admin_password},
    )
    assert response.status_code == 200


async def test_wrong_password_and_unknown_email_look_identical(
    client: AsyncClient, tenant_a: SeededTenant
):
    wrong = await login(client, tenant_a, password="nope")
    unknown = await client.post(
        "/auth/login", json={"email": "ghost@example.com", "password": "whatever"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_login_rejects_unknown_fields(client: AsyncClient, tenant_a: SeededTenant):
    response = await client.post(
        "/auth/login",
        json={"email": tenant_a.admin_email, "password": "x", "tenant_id": str(tenant_a.id)},
    )
    assert response.status_code == 422


async def test_inactive_admin_cannot_login(client: AsyncClient, admin_engine: AsyncEngine):
    tenant = await seed_tenant(admin_engine, "C")
    try:
        async with admin_engine.begin() as conn:
            await conn.execute(
                text("UPDATE admin_users SET is_active = false WHERE id = :id"),
                {"id": tenant.admin_id},
            )
        assert (await login(client, tenant)).status_code == 401
    finally:
        await delete_tenant(admin_engine, tenant.id)


async def test_refresh_rotates_tokens(client: AsyncClient, tenant_a: SeededTenant):
    first = (await login(client, tenant_a)).json()
    response = await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert response.status_code == 200
    second = response.json()
    assert second["refresh_token"] != first["refresh_token"]
    claims = tokens.decode_access_token(second["access_token"])
    assert claims.tenant_id == tenant_a.id


async def test_refresh_reuse_revokes_the_whole_family(client: AsyncClient, tenant_a: SeededTenant):
    first = (await login(client, tenant_a)).json()
    second = (
        await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    ).json()

    replay = await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401

    successor = await client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert successor.status_code == 401


@pytest.mark.parametrize("kind", ["access", "garbage", "expired"])
async def test_refresh_rejects_bad_tokens(client: AsyncClient, tenant_a: SeededTenant, kind: str):
    if kind == "access":
        token = tokens.create_access_token(tenant_a.admin_id, tenant_a.id)
    elif kind == "expired":
        token = tokens.create_refresh_token(
            tenant_a.admin_id,
            tenant_a.admin_id,
            tenant_a.admin_id,
            tenant_a.id,
            ttl=timedelta(seconds=-1),
        )
    else:
        token = "not.a.token"
    response = await client.post("/auth/refresh", json={"refresh_token": token})
    assert response.status_code == 401
