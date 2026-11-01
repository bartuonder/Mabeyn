import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mabeyn.db.models import DocumentSource, DocumentStatus, MessageRole, RiskLevel


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


class ApiKeyOut(Response):
    id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    key: str


class SessionRequest(StrictRequest):
    customer_id: str = Field(min_length=1, max_length=128)


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
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class ToolCreate(StrictRequest):
    name: str = Field(min_length=3, max_length=64)
    description: str = Field(min_length=3, max_length=1000)
    json_schema: dict[str, Any]
    risk_level: RiskLevel
    http_method: str = Field(pattern="^(GET|POST|PUT|PATCH|DELETE)$")
    url_template: str = Field(min_length=8, max_length=2048)


class ToolOut(Response):
    id: uuid.UUID
    name: str
    description: str
    json_schema: dict[str, Any]
    risk_level: RiskLevel
    http_method: str
    url_template: str
    created_at: datetime


class ChatRequest(StrictRequest):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = None
