import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from mabeyn.db import models as m


@dataclass(frozen=True)
class SeededTenant:
    id: uuid.UUID
    admin_email: str
    admin_password: str
    admin_id: uuid.UUID
    document_id: uuid.UUID
    conversation_id: uuid.UUID
    tool_id: uuid.UUID


async def seed_tenant(
    engine: AsyncEngine, label: str, password: str = "", password_hash: str = "x"
) -> SeededTenant:
    """Create one tenant with a row in every tenant-scoped table (owner connection)."""
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSession(engine, expire_on_commit=False) as s, s.begin():
        tenant = m.Tenant(name=f"Tenant {label}", slug=f"{label.lower()}-{suffix}")
        s.add(tenant)
        await s.flush()
        tid = tenant.id

        admin = m.AdminUser(
            tenant_id=tid,
            email=f"{label.lower()}-{suffix}@example.com",
            password_hash=password_hash,
        )
        document = m.Document(
            tenant_id=tid, title=f"Doc {label}", source_type=m.DocumentSource.MARKDOWN
        )
        conversation = m.Conversation(tenant_id=tid, customer_id=f"customer-{label}")
        tool = m.ToolDefinition(
            tenant_id=tid,
            name=f"tool_{label}",
            description="demo",
            json_schema={"type": "object"},
            risk_level=m.RiskLevel.READ,
            http_method="GET",
            url_template="https://example.com/{id}",
        )
        s.add_all([admin, document, conversation, tool])
        await s.flush()

        call = m.ToolCall(
            tenant_id=tid,
            conversation_id=conversation.id,
            tool_name=tool.name,
            arguments={},
            status=m.ToolCallStatus.SUCCESS,
        )
        s.add(call)
        await s.flush()
        s.add_all(
            [
                m.RefreshToken(
                    tenant_id=tid,
                    admin_user_id=admin.id,
                    family_id=uuid.uuid4(),
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                ),
                m.ApiKey(
                    tenant_id=tid, name="key", prefix="mbn_seed", key_hash=uuid.uuid4().hex * 2
                ),
                m.Chunk(
                    tenant_id=tid,
                    document_id=document.id,
                    position=0,
                    content="text",
                    context_header="header",
                    token_count=1,
                ),
                m.Message(
                    tenant_id=tid,
                    conversation_id=conversation.id,
                    role=m.MessageRole.USER,
                    content="hello",
                ),
                m.Approval(
                    tenant_id=tid,
                    conversation_id=conversation.id,
                    tool_call_id=call.id,
                    payload={},
                ),
                m.SecurityEvent(
                    tenant_id=tid,
                    layer="input",
                    category="test",
                    action=m.SecurityAction.FLAGGED,
                ),
            ]
        )
        return SeededTenant(
            id=tid,
            admin_email=admin.email,
            admin_password=password,
            admin_id=admin.id,
            document_id=document.id,
            conversation_id=conversation.id,
            tool_id=tool.id,
        )


async def delete_tenant(engine: AsyncEngine, tenant_id: uuid.UUID) -> None:
    async with AsyncSession(engine) as s, s.begin():
        await s.execute(delete(m.Tenant).where(m.Tenant.id == tenant_id))
