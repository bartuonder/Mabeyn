import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from mabeyn.db.provisioning import create_tenant
from tests.factories import delete_tenant


async def test_provisioned_admin_can_log_in(client: AsyncClient, admin_engine: AsyncEngine):
    suffix = uuid.uuid4().hex[:8]
    email = f"Owner-{suffix}@Example.com"
    tenant_id = await create_tenant(
        admin_engine, "Acme", f"acme-{suffix}", email, "a-long-enough-password"
    )
    try:
        response = await client.post(
            "/auth/login", json={"email": email, "password": "a-long-enough-password"}
        )
        assert response.status_code == 200
    finally:
        await delete_tenant(admin_engine, tenant_id)


@pytest.mark.parametrize("slug", ["", "ab", "Has Space", "UPPER", "-lead-dash"])
async def test_invalid_slug_is_rejected(admin_engine: AsyncEngine, slug: str):
    with pytest.raises(ValueError, match="slug"):
        await create_tenant(admin_engine, "X", slug, "x@example.com", "a-long-enough-password")


async def test_short_password_is_rejected(admin_engine: AsyncEngine):
    with pytest.raises(ValueError, match="password"):
        await create_tenant(admin_engine, "X", "valid-slug", "x@example.com", "short")


async def test_duplicate_slug_is_rejected(admin_engine: AsyncEngine):
    suffix = uuid.uuid4().hex[:8]
    slug = f"dup-{suffix}"
    first = await create_tenant(
        admin_engine, "One", slug, f"one-{suffix}@example.com", "a-long-enough-password"
    )
    try:
        with pytest.raises(IntegrityError):
            await create_tenant(
                admin_engine, "Two", slug, f"two-{suffix}@example.com", "a-long-enough-password"
            )
    finally:
        await delete_tenant(admin_engine, first)
