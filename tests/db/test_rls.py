import uuid

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError

from mabeyn.db import models as m
from mabeyn.db.session import anonymous_session, tenant_session
from tests.factories import SeededTenant

TENANT_MODELS = [
    m.AdminUser,
    m.RefreshToken,
    m.ApiKey,
    m.Document,
    m.Chunk,
    m.Conversation,
    m.Message,
    m.ToolDefinition,
    m.ToolCall,
    m.Approval,
    m.SecurityEvent,
]


@pytest.mark.parametrize("model", TENANT_MODELS)
async def test_tenant_sees_only_own_rows(model, tenant_a: SeededTenant, tenant_b: SeededTenant):
    async with tenant_session(tenant_a.id) as s:
        tenant_ids = set((await s.scalars(select(model.tenant_id))).all())
    assert tenant_ids == {tenant_a.id}


@pytest.mark.parametrize("model", TENANT_MODELS)
async def test_no_context_sees_nothing(model, tenant_a: SeededTenant):
    async with anonymous_session() as s:
        count = await s.scalar(select(func.count()).select_from(model))
    assert count == 0


async def test_cannot_read_other_tenant_row_by_id(tenant_a: SeededTenant, tenant_b: SeededTenant):
    async with tenant_session(tenant_a.id) as s:
        assert await s.get(m.Document, tenant_b.document_id) is None
        assert await s.get(m.Conversation, tenant_b.conversation_id) is None
        assert await s.get(m.ToolDefinition, tenant_b.tool_id) is None


async def test_insert_for_other_tenant_is_rejected(tenant_a: SeededTenant, tenant_b: SeededTenant):
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(tenant_a.id) as s:
            s.add(m.Conversation(tenant_id=tenant_b.id, customer_id="intruder"))
            await s.flush()


async def test_update_and_delete_other_tenant_row_is_noop(
    tenant_a: SeededTenant, tenant_b: SeededTenant
):
    async with tenant_session(tenant_a.id) as s:
        updated = await s.execute(
            update(m.Document).where(m.Document.id == tenant_b.document_id).values(title="pwned")
        )
        deleted = await s.execute(delete(m.Document).where(m.Document.id == tenant_b.document_id))
    assert updated.rowcount == 0  # type: ignore[attr-defined]
    assert deleted.rowcount == 0  # type: ignore[attr-defined]

    async with tenant_session(tenant_b.id) as s:
        document = await s.get(m.Document, tenant_b.document_id)
    assert document is not None and document.title == "Doc B"


async def test_tenants_table_is_scoped_and_read_only(
    tenant_a: SeededTenant, tenant_b: SeededTenant
):
    async with tenant_session(tenant_a.id) as s:
        ids = (await s.scalars(select(m.Tenant.id))).all()
    assert ids == [tenant_a.id]

    with pytest.raises(DBAPIError, match="permission denied"):
        async with tenant_session(tenant_a.id) as s:
            s.add(m.Tenant(name="rogue", slug=f"rogue-{uuid.uuid4().hex[:8]}"))
            await s.flush()


async def test_tenant_context_does_not_leak_across_transactions(tenant_a: SeededTenant):
    async with tenant_session(tenant_a.id) as s:
        assert await s.scalar(select(func.count()).select_from(m.Document)) == 1
    for _ in range(5):
        async with anonymous_session() as s:
            assert await s.scalar(select(func.count()).select_from(m.Document)) == 0


async def test_app_role_cannot_bypass_rls(tenant_a: SeededTenant):
    async with anonymous_session() as s:
        row = (
            await s.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    assert tuple(row) == (False, False)
