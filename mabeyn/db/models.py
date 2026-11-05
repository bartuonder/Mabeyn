import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Computed,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, REGCONFIG, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from mabeyn.db.base import Base

EMBEDDING_DIMENSIONS = 1536


class DocumentSource(enum.StrEnum):
    PDF = "pdf"
    MARKDOWN = "markdown"
    HTML = "html"
    URL = "url"


class DocumentStatus(enum.StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class RiskLevel(enum.StrEnum):
    READ = "read"
    WRITE = "write"
    SENSITIVE = "sensitive"


class MessageRole(enum.StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ToolCallStatus(enum.StrEnum):
    PENDING = "pending"
    SUCCESS = "success"
    ERROR = "error"
    REJECTED = "rejected"


class ApprovalStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ConversationStatus(enum.StrEnum):
    OPEN = "open"
    ESCALATED = "escalated"
    CLOSED = "closed"


class ApiKeyKind(enum.StrEnum):
    PUBLISHABLE = "publishable"
    SECRET = "secret"  # noqa: S105


class SecurityAction(enum.StrEnum):
    BLOCKED = "blocked"
    FLAGGED = "flagged"


def _enum(cls: type[enum.StrEnum]) -> Enum:
    return Enum(
        cls,
        name=cls.__name__.lower(),
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [m.value for m in members],
    )


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TenantModel(Base):
    """Base for every table that belongs to a tenant."""

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdminUser(TenantModel):
    __tablename__ = "admin_users"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class RefreshToken(TenantModel):
    __tablename__ = "refresh_tokens"

    admin_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("admin_users.id", ondelete="CASCADE"), index=True
    )
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKey(TenantModel):
    __tablename__ = "api_keys"

    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[ApiKeyKind] = mapped_column(
        _enum(ApiKeyKind), server_default=ApiKeyKind.SECRET.value
    )
    allowed_origins: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Document(TenantModel):
    __tablename__ = "documents"

    title: Mapped[str] = mapped_column(String(500))
    source_type: Mapped[DocumentSource] = mapped_column(_enum(DocumentSource))
    source_uri: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[DocumentStatus] = mapped_column(
        _enum(DocumentStatus), server_default=DocumentStatus.PENDING.value
    )
    error: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(8), server_default="tr")
    effective_date: Mapped[date | None] = mapped_column(Date)
    raw_content: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)


class Chunk(TenantModel):
    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint("document_id", "position"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    context_header: Mapped[str] = mapped_column(Text)
    section: Mapped[str | None] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)
    token_count: Mapped[int] = mapped_column(Integer)
    flagged: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    injection_score: Mapped[float | None] = mapped_column(Float)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    fts_config: Mapped[str] = mapped_column(REGCONFIG, server_default=text("'simple'::regconfig"))
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector(fts_config, context_header || ' ' || content)", persisted=True),
    )


class Conversation(TenantModel):
    __tablename__ = "conversations"

    customer_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[ConversationStatus] = mapped_column(
        _enum(ConversationStatus), server_default=ConversationStatus.OPEN.value
    )
    escalation_reason: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    summarized_messages: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    memory_extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(TenantModel):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[MessageRole] = mapped_column(_enum(MessageRole))
    content: Mapped[str] = mapped_column(Text)
    blocked: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    input_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    llm_calls: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default=text("0"))
    cached: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )


class ToolDefinition(TenantModel):
    __tablename__ = "tool_definitions"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text)
    json_schema: Mapped[dict[str, Any]] = mapped_column(JSONB)
    risk_level: Mapped[RiskLevel] = mapped_column(_enum(RiskLevel))
    http_method: Mapped[str] = mapped_column(String(10))
    url_template: Mapped[str] = mapped_column(String(2048))
    requires_identity: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class ToolCall(TenantModel):
    __tablename__ = "tool_calls"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    tool_name: Mapped[str] = mapped_column(String(100))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[ToolCallStatus] = mapped_column(_enum(ToolCallStatus))
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class Approval(TenantModel):
    __tablename__ = "approvals"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    tool_call_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tool_calls.id", ondelete="SET NULL")
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        _enum(ApprovalStatus), server_default=ApprovalStatus.PENDING.value
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SecurityEvent(TenantModel):
    __tablename__ = "security_events"

    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    layer: Mapped[str] = mapped_column(String(32))
    category: Mapped[str] = mapped_column(String(64))
    action: Mapped[SecurityAction] = mapped_column(_enum(SecurityAction))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class TenantSettings(Base):
    __tablename__ = "tenant_settings"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    firewall_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    injection_threshold: Mapped[float] = mapped_column(Float, server_default=text("0.7"))
    write_rate_limit: Mapped[int] = mapped_column(Integer, server_default=text("5"))
    write_rate_window_minutes: Mapped[int] = mapped_column(Integer, server_default=text("60"))
    tool_allowlist: Mapped[list[str] | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AnswerCacheEntry(TenantModel):
    __tablename__ = "answer_cache"
    __table_args__ = (UniqueConstraint("tenant_id", "prompt_hash", "question_hash"),)

    question_hash: Mapped[str] = mapped_column(String(64))
    prompt_hash: Mapped[str] = mapped_column(String(64))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    hits: Mapped[int] = mapped_column(Integer, server_default=text("0"))
