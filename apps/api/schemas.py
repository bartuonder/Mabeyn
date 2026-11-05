import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from mabeyn.db.models import (
    ApiKeyKind,
    ApprovalStatus,
    ConversationStatus,
    DocumentSource,
    DocumentStatus,
    MessageRole,
    RiskLevel,
    SecurityAction,
)
from mabeyn.memory.schema import MemoryCategory


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Response(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginRequest(StrictRequest):
    email: str = Field(min_length=3, max_length=320)
    password: str


class RefreshRequest(StrictRequest):
    refresh_token: str


class TokenResponse(Response):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int


class ApiKeyCreate(StrictRequest):
    name: str = Field(min_length=1, max_length=100)
    kind: ApiKeyKind = ApiKeyKind.SECRET
    allowed_origins: list[str] = Field(default_factory=list, max_length=20)


class ApiKeyOut(Response):
    id: uuid.UUID
    name: str
    prefix: str
    kind: ApiKeyKind
    allowed_origins: list[str]
    created_at: datetime
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    key: str


class SessionRequest(StrictRequest):
    customer_id: str | None = Field(default=None, min_length=1, max_length=128)


class SessionResponse(Response):
    token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int


class DocumentOut(Response):
    id: uuid.UUID
    title: str
    source_type: DocumentSource
    status: DocumentStatus
    error: str | None
    size_bytes: int | None
    language: str
    effective_date: date | None
    created_at: datetime


class UrlDocumentCreate(StrictRequest):
    url: str = Field(min_length=8, max_length=2048)
    title: str | None = Field(default=None, max_length=500)
    effective_date: date | None = None


class MessageOut(Response):
    id: uuid.UUID
    role: MessageRole
    content: str
    citations: list[dict[str, Any]]
    created_at: datetime


class ConversationOut(Response):
    id: uuid.UUID
    customer_id: str
    status: ConversationStatus
    escalation_reason: str | None
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class ConversationClosed(Response):
    status: ConversationStatus
    memories_saved: int
    memories_blocked: int


class MemoryOut(Response):
    key: str
    category: MemoryCategory
    text: str
    updated_at: datetime | None


class MemoryDeleted(Response):
    deleted: int


class ToolCreate(StrictRequest):
    name: str = Field(min_length=3, max_length=64)
    description: str = Field(min_length=3, max_length=1000)
    json_schema: dict[str, Any]
    risk_level: RiskLevel
    http_method: str = Field(pattern="^(GET|POST|PUT|PATCH|DELETE)$")
    url_template: str = Field(min_length=8, max_length=2048)
    requires_identity: bool = True


class ToolOut(Response):
    id: uuid.UUID
    name: str
    description: str
    json_schema: dict[str, Any]
    risk_level: RiskLevel
    http_method: str
    url_template: str
    requires_identity: bool
    created_at: datetime


class ChatRequest(StrictRequest):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = None


class ApprovalDecision(StrictRequest):
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=500)


class ApprovalOut(Response):
    id: uuid.UUID
    conversation_id: uuid.UUID
    tool_call_id: uuid.UUID | None
    status: ApprovalStatus
    payload: dict[str, Any]
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    expires_at: datetime | None
    created_at: datetime


class ApprovalResolved(ApprovalOut):
    answer: str


class FirewallSettingsOut(Response):
    firewall_enabled: bool
    injection_threshold: float
    write_rate_limit: int
    write_rate_window_minutes: int
    tool_allowlist: list[str] | None


class FirewallSettingsUpdate(StrictRequest):
    firewall_enabled: bool | None = None
    injection_threshold: float | None = Field(default=None, ge=0.05, le=1.0)
    write_rate_limit: int | None = Field(default=None, ge=0, le=1000)
    write_rate_window_minutes: int | None = Field(default=None, ge=1, le=1440)
    tool_allowlist: list[str] | None = Field(default=None, max_length=100)


class SecurityEventOut(Response):
    id: uuid.UUID
    created_at: datetime
    conversation_id: uuid.UUID | None
    layer: str
    category: str
    action: SecurityAction
    detail: dict[str, Any]


class UsageDay(Response):
    day: date
    messages: int
    input_tokens: int
    output_tokens: int
    llm_calls: int
    cost_usd: float
    cached_messages: int
    average_latency_ms: float | None


class UsageReport(Response):
    days: list[UsageDay]
    total_cost_usd: float
    total_messages: int
